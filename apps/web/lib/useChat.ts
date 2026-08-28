"use client";

/**
 * Le fil de chat d'un agent, côté navigateur (ticket #85).
 *
 * Même modèle que `useControlTower` : l'historique se charge par le REST
 * (`GET /api/chat/{agent}`) puis le WebSocket signale chaque `chat.message`
 * du fil. Le backend persiste **avant** de diffuser (maestro/controltower/
 * chat.py) : à réception d'un événement le REST est déjà à jour, le hook
 * recharge donc le fil (rechargements coalescés) plutôt que de projeter
 * l'événement. La connexion se rétablit seule (backoff plafonné) et chaque
 * reconnexion recharge le fil — les messages manqués pendant une coupure
 * sont rattrapés.
 *
 * **Le seul flux de l'application qui reste transverse** (#281), et c'est le
 * contrat de #277 qui l'impose : un `chat.message` ne porte pas de `projet_id`
 * (maestro/controltower/chat.py), donc une socket cadrée sur un projet ne le
 * recevrait **jamais** — le fil se figerait sans rien dire, chaque message
 * n'apparaissant qu'au rechargement suivant. Ce n'est pas une exception de
 * confort : le chat parle de l'**outil** et non du projet (voir le répondeur
 * d'assistance, `maestro/controltower/app.py`), il n'a donc pas de périmètre à
 * respecter. Toute vue qui, elle, montre le **travail** passe par
 * `useControlTower` et son projet actif.
 *
 * **Ce que le fil ne cadre pas, il le transporte** (#683). `projetId` est le
 * projet de la fenêtre : il part avec **l'envoi**, jamais avec la lecture du fil
 * ni avec la socket — rien de ce qui précède ne change. Il ne sert qu'à ce qu'un
 * message **ouvre** : un run dicté à l'orchestration appartient au projet où on
 * l'a demandé, faute de quoi il n'entre dans la vue d'aucun projet et devient
 * introuvable à l'écran (le défaut de #683, devenu le cas nominal depuis que le
 * chat est la seule porte d'entrée, #666). Le hook ne va pas le chercher
 * lui-même : il le reçoit de l'appelant, seul à savoir s'il est monté sous un
 * projet — l'assistant flottant et l'onglet Chat d'un agent n'ouvrent aucun run
 * et n'ont donc rien à passer.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  chargerFilChat,
  envoyerMessageChat,
  urlEvenements,
  PORTEE_TOUS,
} from "./api";
import {
  EVENEMENT_CHAT_MESSAGE,
  type Evenement,
  type MessageChat,
  type SourceDeclaree,
} from "./types";

/** Fenêtre de coalescence des rechargements sur rafale d'événements (ms). */
const DELAI_RECHARGEMENT_MS = 150;

/** Plafond du backoff de reconnexion WebSocket (ms). */
const RECONNEXION_MAX_MS = 10_000;

export type Chat = {
  /** Le fil persisté, dans l'ordre d'écriture (le plus récent en dernier). */
  messages: MessageChat[];
  /** WebSocket ouverte : les messages arrivent en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** Fil illisible au dernier chargement (null si tout va bien). */
  erreur: string | null;
  /** Envoi en cours : le message est parti, la réponse se fait attendre. */
  envoi: boolean;
  /**
   * Envoie un message à l'agent, avec les **sources** qu'il embarque (#482).
   * Rejette si la réponse n'a pas pu être produite, ou sur une `ErreurSource`
   * quand une source est refusée — motif et index compris, pour que le fil
   * l'affiche à l'endroit du geste refusé.
   */
  envoyer: (contenu: string, sources?: SourceDeclaree[]) => Promise<void>;
};

export function useChat(agent: string, projetId: string | null = null): Chat {
  const [messages, setMessages] = useState<MessageChat[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [envoi, setEnvoi] = useState(false);

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    try {
      const fil = await chargerFilChat(agent);
      setMessages(fil.messages);
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, [agent]);

  const planifierRechargement = useCallback(() => {
    if (rechargementPrevu.current !== null) return;
    rechargementPrevu.current = setTimeout(() => {
      rechargementPrevu.current = null;
      void recharger();
    }, DELAI_RECHARGEMENT_MS);
  }, [recharger]);

  useEffect(() => {
    let abandonne = false;
    let socket: WebSocket | null = null;
    let reconnexion: ReturnType<typeof setTimeout> | null = null;
    let tentatives = 0;

    // Chargement initial différé d'un tick (même mécanique que les rafales) :
    // l'effet lui-même ne déclenche aucun setState synchrone.
    planifierRechargement();

    const connecter = () => {
      if (abandonne) return;
      // Transverse à dessein — voir l'en-tête : `chat.message` ne porte pas de
      // projet, une socket cadrée ne le verrait pas passer.
      socket = new WebSocket(urlEvenements(PORTEE_TOUS));
      socket.onopen = () => {
        tentatives = 0;
        setConnecte(true);
        // Rattrape ce qui a pu se passer entre le REST initial et l'ouverture
        // de la socket (ou pendant une coupure).
        void recharger();
      };
      socket.onmessage = (message: MessageEvent<string>) => {
        let evenement: Evenement;
        try {
          evenement = JSON.parse(message.data) as Evenement;
        } catch {
          return; // trame illisible : on l'ignore, le REST reste la vérité
        }
        // Le bus est commun à toute la Control Tower : seul un message de ce
        // fil justifie un rechargement.
        if (
          evenement.type !== EVENEMENT_CHAT_MESSAGE ||
          evenement.agent !== agent
        )
          return;
        planifierRechargement();
      };
      socket.onclose = () => {
        setConnecte(false);
        if (abandonne) return;
        tentatives += 1;
        const delai = Math.min(1000 * 2 ** (tentatives - 1), RECONNEXION_MAX_MS);
        reconnexion = setTimeout(connecter, delai);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connecter();

    return () => {
      abandonne = true;
      if (reconnexion !== null) clearTimeout(reconnexion);
      if (rechargementPrevu.current !== null) {
        clearTimeout(rechargementPrevu.current);
        rechargementPrevu.current = null;
      }
      socket?.close();
    };
  }, [agent, recharger, planifierRechargement]);

  const envoyer = useCallback(
    async (contenu: string, sources: SourceDeclaree[] = []) => {
      setEnvoi(true);
      try {
        await envoyerMessageChat(agent, contenu, sources, projetId);
        // La paire message/réponse arrivera aussi par le WebSocket ; ce
        // rechargement direct rend l'UI réactive même si la socket est coupée.
        await recharger();
      } finally {
        setEnvoi(false);
      }
    },
    [agent, projetId, recharger],
  );

  return { messages, connecte, chargement, erreur, envoi, envoyer };
}

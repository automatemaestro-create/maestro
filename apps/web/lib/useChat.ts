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
  /** Envoie un message à l'agent ; rejette si la réponse n'a pas pu être produite. */
  envoyer: (contenu: string) => Promise<void>;
};

export function useChat(agent: string): Chat {
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
    async (contenu: string) => {
      setEnvoi(true);
      try {
        await envoyerMessageChat(agent, contenu);
        // La paire message/réponse arrivera aussi par le WebSocket ; ce
        // rechargement direct rend l'UI réactive même si la socket est coupée.
        await recharger();
      } finally {
        setEnvoi(false);
      }
    },
    [agent, recharger],
  );

  return { messages, connecte, chargement, erreur, envoi, envoyer };
}

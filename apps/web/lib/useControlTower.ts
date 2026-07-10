"use client";

/**
 * L'état temps réel de la Control Tower, côté navigateur.
 *
 * Même modèle que le backend (maestro/controltower) : l'état courant se charge
 * par le REST, puis le WebSocket signale chaque événement. La pompe du backend
 * projette l'état **avant** de diffuser, donc à réception d'un événement le
 * REST est déjà à jour : plutôt que de dupliquer la projection en TypeScript,
 * le hook recharge tâches et agents (rechargements coalescés en rafale).
 *
 * La connexion WebSocket se rétablit seule (backoff plafonné) et chaque
 * reconnexion recharge l'état — les événements manqués pendant la coupure
 * sont ainsi rattrapés.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  chargerAgents,
  chargerTaches,
  chargerValidations,
  deciderValidation,
  reassignerTache,
  urlEvenements,
} from "./api";
import type { EtatAgent, Evenement, Tache, Validation } from "./types";

/** Longueur du fil d'activité conservé côté client. */
const MAX_EVENEMENTS = 50;

/** Fenêtre de coalescence des rechargements sur rafale d'événements (ms). */
const DELAI_RECHARGEMENT_MS = 150;

/** Plafond du backoff de reconnexion WebSocket (ms). */
const RECONNEXION_MAX_MS = 10_000;

export type ControlTower = {
  taches: Tache[];
  agents: EtatAgent[];
  evenements: Evenement[];
  /** Demandes de validation humaine (#48), en attente comme tranchées. */
  validations: Validation[];
  /** WebSocket ouverte : les mises à jour arrivent en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** API injoignable au dernier chargement (null si tout va bien). */
  erreur: string | null;
  reassigner: (tacheId: string, agent: string) => Promise<void>;
  /** Tranche une demande de validation : le moteur reprend ou annule la tâche. */
  decider: (tacheId: string, approuve: boolean) => Promise<void>;
};

export function useControlTower(): ControlTower {
  const [taches, setTaches] = useState<Tache[]>([]);
  const [agents, setAgents] = useState<EtatAgent[]>([]);
  const [evenements, setEvenements] = useState<Evenement[]>([]);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    try {
      const [nouvellesTaches, nouveauxAgents, nouvellesValidations] =
        await Promise.all([chargerTaches(), chargerAgents(), chargerValidations()]);
      setTaches(nouvellesTaches);
      setAgents(nouveauxAgents);
      setValidations(nouvellesValidations);
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, []);

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
      socket = new WebSocket(urlEvenements());
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
        setEvenements((precedents) =>
          [evenement, ...precedents].slice(0, MAX_EVENEMENTS),
        );
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
  }, [recharger, planifierRechargement]);

  const reassigner = useCallback(
    async (tacheId: string, agent: string) => {
      await reassignerTache(tacheId, agent);
      // L'événement de réassignation arrivera aussi par le WebSocket ; ce
      // rechargement direct rend l'UI réactive même si la socket est coupée.
      await recharger();
    },
    [recharger],
  );

  const decider = useCallback(
    async (tacheId: string, approuve: boolean) => {
      await deciderValidation(tacheId, approuve);
      // Même mécanique que la réassignation : le WebSocket confirmera, le
      // rechargement direct fait disparaître la demande sans attendre.
      await recharger();
    },
    [recharger],
  );

  return {
    taches,
    agents,
    evenements,
    validations,
    connecte,
    chargement,
    erreur,
    reassigner,
    decider,
  };
}

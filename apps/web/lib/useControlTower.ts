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
 *
 * **Tout ce qu'il rend est cadré sur une portée projet** (#281) : les lectures
 * la passent en paramètre et la socket la déclare à l'ouverture, si bien qu'un
 * événement d'un autre projet n'entre jamais dans la file (#277). Une seule
 * exception, assumée et documentée : `GET /api/agents`, qui décrit le **parc du
 * poste** et non le travail d'un projet (voir `lib/api`, docs/05 §2.3).
 *
 * La portée ne change **jamais en place** dans l'application : le shell remonte
 * le fournisseur d'état sur un changement de projet (`key`, `components/Shell`),
 * ce qui remet à zéro d'un coup l'état d'ici *et* celui que les pages tiennent
 * elles-mêmes — filtres du Journal, période des Coûts. C'est la garantie
 * « aucune donnée de l'ancien projet ne subsiste » du critère #281, et elle est
 * plus large que ce que ce hook pourrait tenir seul.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  chargerAgents,
  chargerCoutExecution,
  chargerTaches,
  chargerValidations,
  deciderValidation,
  reassignerTache,
  reglerCapaciteAgent,
  urlEvenements,
  type PorteeProjet,
} from "./api";
import type {
  CoutExecution,
  EtatAgent,
  Evenement,
  Tache,
  Validation,
} from "./types";

/**
 * Longueur du fil d'activité conservé côté client. Exporté depuis #249 : le
 * Journal **dit** ce qu'il montre (« les N derniers événements »), et le chiffre
 * doit venir de la mécanique qui le borne, pas d'une prose qui vieillirait.
 */
export const MAX_EVENEMENTS = 50;

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
  /** Grands livres des exécutions connues (#57) : coût par tâche et agrégat. */
  couts: CoutExecution[];
  /** WebSocket ouverte : les mises à jour arrivent en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** API injoignable au dernier chargement (null si tout va bien). */
  erreur: string | null;
  reassigner: (tacheId: string, agent: string) => Promise<void>;
  /** Tranche une demande de validation : le moteur reprend ou annule la tâche. */
  decider: (tacheId: string, approuve: boolean) => Promise<void>;
  /** Règle la capacité d'un agent (#86) : activer/désactiver, instances. */
  reglerCapacite: (
    nom: string,
    reglage: { actif?: boolean; instances?: number },
  ) => Promise<void>;
};

export function useControlTower(portee: PorteeProjet): ControlTower {
  const [taches, setTaches] = useState<Tache[]>([]);
  const [agents, setAgents] = useState<EtatAgent[]>([]);
  const [evenements, setEvenements] = useState<Evenement[]>([]);
  const [validations, setValidations] = useState<Validation[]>([]);
  const [couts, setCouts] = useState<CoutExecution[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    try {
      const [nouvellesTaches, nouveauxAgents, nouvellesValidations] =
        await Promise.all([
          chargerTaches(portee),
          // Sans portée : le parc d'agents est du poste, pas du projet.
          chargerAgents(),
          chargerValidations(portee),
        ]);
      // Les grands livres (#57) se chargent après les tâches : les run_id
      // connus en sont dérivés (le backend a une exécution pour chacun).
      const runIds = [
        ...new Set(nouvellesTaches.map((t) => t.run_id).filter(Boolean)),
      ];
      const nouveauxCouts = await Promise.all(runIds.map(chargerCoutExecution));
      setTaches(nouvellesTaches);
      setAgents(nouveauxAgents);
      setValidations(nouvellesValidations);
      setCouts(nouveauxCouts);
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, [portee]);

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
      // La portée est déclarée à l'ouverture (#277) : le tri se fait à l'entrée
      // de la file côté backend, un événement d'un autre projet n'arrive donc
      // jamais ici — il n'y a rien à refiltrer côté client.
      socket = new WebSocket(urlEvenements(portee));
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
  }, [recharger, planifierRechargement, portee]);

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

  const reglerCapacite = useCallback(
    async (nom: string, reglage: { actif?: boolean; instances?: number }) => {
      await reglerCapaciteAgent(nom, reglage);
      // Même mécanique : l'événement `agent.capacite` arrivera par le
      // WebSocket, le rechargement direct met la fiche à jour sans attendre.
      await recharger();
    },
    [recharger],
  );

  return {
    taches,
    agents,
    evenements,
    validations,
    couts,
    connecte,
    chargement,
    erreur,
    reassigner,
    decider,
    reglerCapacite,
  };
}

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
  chargerExecutions,
  chargerTaches,
  chargerValidations,
  deciderBrief,
  deciderValidation,
  reassignerTache,
  reglerCapaciteAgent,
  relancerExecution,
  repondreBrief,
  reprendreExecution,
  suspendreExecution,
  urlEvenements,
  type PorteeProjet,
} from "./api";
import type {
  CoutExecution,
  DecisionBrief,
  EtatAgent,
  Evenement,
  ResumeExecution,
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
  /**
   * Les exécutions du projet actif (#185), résumé seul. Chargées ici depuis #322
   * pour une raison précise : un run **suspendu sur son brief** n'a créé aucune
   * tâche, donc rien de ce que ce hook tenait jusqu'ici ne le montrait — ni le
   * Kanban, ni les grands livres, tous dérivés des tâches. Un run qu'on ne voit
   * pas est un run perdu, et c'est cette liste qui allume le badge d'attente.
   */
  executions: ResumeExecution[];
  /** Grands livres des exécutions connues (#57) : coût par tâche et agrégat. */
  couts: CoutExecution[];
  /** WebSocket ouverte : les mises à jour arrivent en temps réel. */
  connecte: boolean;
  /**
   * Le **pouls du shell** (#475) : un compteur incrémenté à chaque lecture
   * aboutie — donc au chargement initial, à chaque reconnexion et à chaque rafale
   * d'événements coalescée.
   *
   * Il existe pour ce que ce hook ne peut pas tenir : une lecture **cadrée
   * autrement** que sur le projet actif. La vue d'un run charge les tâches de
   * *son* run (`?run=`, #473), que le shell n'a aucune raison de tenir pour toutes
   * les pages — mais elle doit se rafraîchir aux mêmes instants, sans ouvrir une
   * seconde WebSocket ni redupliquer la coalescence des rafales. Elle s'abonne
   * donc à ce compteur (`lib/useTachesRun`).
   *
   * Un compteur et non la référence d'un tableau : « `taches` a changé d'identité »
   * marcherait aujourd'hui et cesserait sans bruit le jour où un rechargement
   * comparerait avant de poser son état. Ici la promesse est explicite — le nombre
   * change quand une lecture vient d'aboutir, et pour aucune autre raison.
   *
   * Incrémenté **aussi** sur échec : une API injoignable est une lecture qui a
   * abouti à quelque chose, et les vues qui en dépendent doivent réessayer au même
   * rythme que le shell plutôt que rester figées sur leur dernière erreur.
   */
  revision: number;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** API injoignable au dernier chargement (null si tout va bien). */
  erreur: string | null;
  reassigner: (tacheId: string, agent: string) => Promise<void>;
  /** Tranche une demande de validation : le moteur reprend ou annule la tâche. */
  decider: (tacheId: string, approuve: boolean) => Promise<void>;
  /**
   * Tranche le brief d'un run (#320) : approuver tel quel ou corrigé, ou refuser.
   * Le run reprend sur la décomposition, ou se solde sans qu'aucune tâche n'ait
   * été créée.
   */
  trancherBrief: (runId: string, decision: DecisionBrief) => Promise<void>;
  /**
   * Répond aux questions de clarification d'un brief (#321) : le run repart le
   * rédiger en les intégrant. Appariées **par position** aux questions du brief.
   */
  repondreAuBrief: (runId: string, reponses: string[]) => Promise<void>;
  /**
   * Rejoue un run interrompu sur son **brief approuvé** (#349) : le cadrage déjà
   * payé repart sans repasser par la clarification ni par la validation, et le run
   * repris est soldé. Rend le résumé du **nouveau** run — celui qui porte
   * `reprise_de` —, de quoi en afficher l'identifiant sans attendre le rechargement.
   */
  relancerRun: (runId: string) => Promise<ResumeExecution>;
  /**
   * Suspend un run en cours (#477) : aucune tâche nouvelle n'est lancée, celles
   * qui sont en vol vont à leur terme. Le run n'est pas soldé — il bat toujours.
   */
  suspendreRun: (runId: string) => Promise<ResumeExecution>;
  /**
   * Reprend un run suspendu **là où il en était** (#477) — le plan, les tâches
   * déjà faites et le cadrage n'ont pas bougé. À ne pas confondre avec
   * `relancerRun`, qui rejoue un run **mort** depuis son brief et en crée un neuf.
   */
  reprendreRun: (runId: string) => Promise<ResumeExecution>;
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
  const [executions, setExecutions] = useState<ResumeExecution[]>([]);
  const [couts, setCouts] = useState<CoutExecution[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [revision, setRevision] = useState(0);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    try {
      const [
        nouvellesTaches,
        nouveauxAgents,
        nouvellesValidations,
        nouvellesExecutions,
      ] = await Promise.all([
        chargerTaches(portee),
        // Sans portée : le parc d'agents est du poste, pas du projet.
        chargerAgents(),
        chargerValidations(portee),
        chargerExecutions(portee),
      ]);
      // Les grands livres (#57) se chargent après : les run_id connus en sont
      // dérivés. Des **deux** listes depuis #322, et pas des seules tâches — un
      // run arrêté sur son brief n'en a aucune, si bien que sa dépense (la
      // rédaction du brief, déjà payée) restait hors du cumul affiché par la
      // barre supérieure et par la tuile « Dépense ». Un coût engagé qui ne se
      // voit nulle part est précisément ce que le point de contrôle du brief
      // demande de voir.
      const runIds = [
        ...new Set(
          [
            ...nouvellesTaches.map((t) => t.run_id),
            ...nouvellesExecutions.map((e) => e.run_id),
          ].filter(Boolean),
        ),
      ];
      const nouveauxCouts = await Promise.all(runIds.map(chargerCoutExecution));
      setTaches(nouvellesTaches);
      setAgents(nouveauxAgents);
      setValidations(nouvellesValidations);
      setExecutions(nouvellesExecutions);
      setCouts(nouveauxCouts);
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
      // Le pouls bat ici et nulle part ailleurs : une lecture vient de se
      // terminer, quoi qu'elle ait rendu. `recharger` ne dépend pas de `revision`
      // (forme fonctionnelle), donc ce battement ne se réinjecte pas dans l'effet
      // qui l'a déclenché.
      setRevision((precedente) => precedente + 1);
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

  const trancherBrief = useCallback(
    async (runId: string, decision: DecisionBrief) => {
      await deciderBrief(runId, decision);
      // Même mécanique que les autres décisions : l'événement `brief.decision`
      // arrivera par le WebSocket, le rechargement direct fait sortir le run de
      // l'attente sans dépendre de la socket.
      await recharger();
    },
    [recharger],
  );

  const repondreAuBrief = useCallback(
    async (runId: string, reponses: string[]) => {
      await repondreBrief(runId, reponses);
      await recharger();
    },
    [recharger],
  );

  const relancerRun = useCallback(
    async (runId: string) => {
      const nouveau = await relancerExecution(runId);
      // Même mécanique que les décisions : le WebSocket portera le lancement du
      // nouveau run **et** l'issue de celui qui est repris, le rechargement direct
      // fait sortir l'orphelin de la liste sans dépendre de la socket. Deux runs
      // changent d'état ici, ce qui est justement ce qu'un seul rechargement rend.
      await recharger();
      return nouveau;
    },
    [recharger],
  );

  const suspendreRun = useCallback(
    async (runId: string) => {
      const suspendu = await suspendreExecution(runId);
      // Même mécanique que les décisions. Elle compte davantage ici : l'ordre de
      // pause **ne produit aucune tâche** et n'apparaît donc dans aucune vue
      // dérivée — sans ce rechargement, l'écran garderait « En cours » jusqu'au
      // prochain événement du run, c'est-à-dire potentiellement jamais, puisque
      // c'est précisément ce qu'on vient d'arrêter.
      await recharger();
      return suspendu;
    },
    [recharger],
  );

  const reprendreRun = useCallback(
    async (runId: string) => {
      const repris = await reprendreExecution(runId);
      await recharger();
      return repris;
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
    executions,
    couts,
    connecte,
    revision,
    chargement,
    erreur,
    reassigner,
    decider,
    trancherBrief,
    repondreAuBrief,
    relancerRun,
    suspendreRun,
    reprendreRun,
    reglerCapacite,
  };
}

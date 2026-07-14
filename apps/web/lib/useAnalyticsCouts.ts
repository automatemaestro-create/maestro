"use client";

/**
 * La vue coûts & analytics, côté navigateur (ticket #87).
 *
 * Même modèle que `useControlTower` : la vue agrégée se charge par le REST
 * (`GET /api/analytics/couts`) puis le WebSocket signale chaque événement —
 * la pompe du backend projette l'état avant de diffuser, le hook recharge
 * donc la vue (rechargements coalescés) plutôt que d'agréger côté client.
 * La connexion se rétablit seule (backoff plafonné) et chaque reconnexion
 * recharge la vue. Pendant un rechargement, la vue précédente reste servie
 * (`rafraichissement`) : pas de squelette, pas de saut de mise en page.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { chargerAnalyticsCouts, urlEvenements } from "./api";
import type { AnalyticsCouts, PasSerie } from "./types";

/** Fenêtre de coalescence des rechargements sur rafale d'événements (ms). */
const DELAI_RECHARGEMENT_MS = 150;

/** Plafond du backoff de reconnexion WebSocket (ms). */
const RECONNEXION_MAX_MS = 10_000;

/**
 * Une période sélectionnable du tableau de bord : la fenêtre `depuis` (durée
 * glissante, null = tout l'historique projeté) et la granularité des seaux
 * de la série qui va avec.
 */
export type Periode = {
  id: string;
  libelle: string;
  dureeMs: number | null;
  pas: PasSerie;
};

/** Les préréglages de période, du plus court au plus large. */
export const PERIODES: readonly Periode[] = [
  { id: "1h", libelle: "Dernière heure", dureeMs: 3_600_000, pas: "minute" },
  { id: "24h", libelle: "24 heures", dureeMs: 86_400_000, pas: "heure" },
  { id: "7j", libelle: "7 jours", dureeMs: 7 * 86_400_000, pas: "jour" },
  { id: "tout", libelle: "Tout", dureeMs: null, pas: "heure" },
] as const;

export type VueAnalytics = {
  /** La vue agrégée (null tant que le premier chargement n'a pas abouti). */
  vue: AnalyticsCouts | null;
  /** WebSocket ouverte : la vue suit l'orchestration en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** Rechargement en cours : la vue précédente reste affichée, estompée. */
  rafraichissement: boolean;
  /** API injoignable au dernier chargement (null si tout va bien). */
  erreur: string | null;
};

export function useAnalyticsCouts(periode: Periode): VueAnalytics {
  const [vue, setVue] = useState<AnalyticsCouts | null>(null);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [rafraichissement, setRafraichissement] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    setRafraichissement(true);
    try {
      // La borne `depuis` se recalcule à chaque chargement : la fenêtre est
      // glissante (« dernière heure » = l'heure précédant ce rechargement).
      const nouvelle = await chargerAnalyticsCouts({
        ...(periode.dureeMs !== null && {
          depuis: new Date(Date.now() - periode.dureeMs).toISOString(),
        }),
        pas: periode.pas,
      });
      setVue(nouvelle);
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
      setRafraichissement(false);
    }
  }, [periode]);

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

    // Chargement initial (et à chaque changement de période) différé d'un
    // tick : l'effet lui-même ne déclenche aucun setState synchrone.
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
      socket.onmessage = () => {
        // Tout événement du bus peut déplacer les agrégats (statut, activité,
        // message porteur d'usage) : la vue se recharge, coalescée en rafale.
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

  return { vue, connecte, chargement, rafraichissement, erreur };
}

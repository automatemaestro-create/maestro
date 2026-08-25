"use client";

/**
 * Section « Général » des Paramètres (#121) : à quel backend l'interface est
 * branchée, et est-ce qu'il répond.
 *
 * Les deux URL sont inlinées au build (`NEXT_PUBLIC_MAESTRO_API_URL`) : elles se
 * lisent ici mais ne s'éditent pas — les rendre modifiables supposerait un
 * stockage côté client et un rechargement du flux, hors du périmètre de ce lot.
 * Le bouton, lui, est bien un contrôle : il sonde `GET /api/sante`, ce que le
 * badge temps réel ne dit pas (le WebSocket peut être coupé alors que le REST
 * répond, et réciproquement).
 */

import { useState } from "react";

import { chargerSante, urlApi, urlEvenements } from "@/lib/api";
import { useEtatGlobal } from "@/lib/etatGlobal";

import { LigneReglage } from "./SectionParametres";

type Verdict = { ok: boolean; message: string };

export function ParametresGeneral() {
  const { connecte, portee } = useEtatGlobal();
  const [enCours, setEnCours] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | null>(null);

  const tester = async () => {
    setEnCours(true);
    setVerdict(null);
    try {
      const sante = await chargerSante();
      setVerdict({ ok: true, message: `API joignable (statut « ${sante.statut} »).` });
    } catch (e) {
      setVerdict({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setEnCours(false);
    }
  };

  return (
    <div className="flex flex-col">
      <LigneReglage
        libelle="API Control Tower"
        aide={
          <>
            Réglée au build par{" "}
            <code className="font-mono">NEXT_PUBLIC_MAESTRO_API_URL</code>.
          </>
        }
      >
        <code className="font-mono text-sm break-all">{urlApi()}</code>
      </LigneReglage>

      <LigneReglage
        libelle="Flux temps réel"
        aide="Le WebSocket qui pousse les événements — dérivé de l'URL de l'API, et cadré sur le projet actif (#281) : c'est l'URL réellement ouverte, portée comprise, pas un gabarit."
      >
        <div className="flex flex-wrap items-center justify-end gap-2">
          <code className="font-mono text-sm break-all">
            {urlEvenements(portee)}
          </code>
          <span
            className={
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium " +
              (connecte
                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300")
            }
          >
            <span
              className={
                "size-1.5 rounded-full " +
                (connecte
                  ? "bg-emerald-500"
                  : "animate-pulse motion-reduce:animate-none bg-amber-500")
              }
            />
            {connecte ? "Connecté" : "Reconnexion…"}
          </span>
        </div>
      </LigneReglage>

      <LigneReglage
        libelle="Vitalité du service"
        aide={
          verdict === null ? (
            "Interroge GET /api/sante — de quoi distinguer un backend éteint d'un flux coupé."
          ) : (
            <span
              className={
                verdict.ok
                  ? "text-emerald-700 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400"
              }
            >
              {verdict.message}
            </span>
          )
        }
      >
        <button
          type="button"
          onClick={() => void tester()}
          disabled={enCours}
          className="rounded border border-neutral-300 px-3 py-1 text-sm font-medium hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {enCours ? "Test…" : "Tester la connexion"}
        </button>
      </LigneReglage>
    </div>
  );
}

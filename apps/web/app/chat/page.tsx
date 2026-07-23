"use client";

/**
 * La page Chat de la Control Tower (ticket #85, lot 2 de #82) : converser
 * avec chaque agent du catalogue. Branchée sur l'API du lot 1 (#84,
 * `/api/chat`) : l'historique persisté se recharge au retour sur la page,
 * les nouveaux messages arrivent en temps réel par le WebSocket.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilChat } from "@/components/FilChat";
import { chargerCatalogue } from "@/lib/api";
import type { AgentCatalogue } from "@/lib/types";

export default function PageChat() {
  const [fiches, setFiches] = useState<AgentCatalogue[]>([]);
  const [selection, setSelection] = useState<string | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    try {
      const nouvelles = await chargerCatalogue();
      setFiches(nouvelles);
      // Le premier agent est sélectionné d'office ; une sélection existante
      // survit au rechargement (un agent disparu retombe sur le premier).
      setSelection((courante) =>
        courante !== null && nouvelles.some((f) => f.nom === courante)
          ? courante
          : (nouvelles[0]?.nom ?? null),
      );
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, []);

  // Chargement initial différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => void recharger(), 0);
    return () => clearTimeout(tick);
  }, [recharger]);

  const fiche = fiches.find((f) => f.nom === selection);

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement du catalogue…</p>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row">
          <nav
            aria-label="Agents du catalogue"
            className="flex flex-row flex-wrap content-start gap-2 lg:w-56 lg:shrink-0 lg:flex-col"
          >
            {fiches.map((f) => (
              <CarteAgent
                key={f.nom}
                fiche={f}
                selectionnee={f.nom === selection}
                selectionner={() => setSelection(f.nom)}
              />
            ))}
          </nav>
          {fiche !== undefined ? (
            // `key` : changer d'agent remonte un fil neuf (état de saisie
            // et WebSocket propres à chaque conversation).
            <FilChat key={fiche.nom} agent={fiche.nom} role={fiche.role} />
          ) : (
            <p className="text-sm text-neutral-500">
              Aucun agent au catalogue — en créer un depuis la page{" "}
              <Link
                href="/catalogue"
                className="font-medium text-neutral-900 underline dark:text-neutral-200"
              >
                🧩 Agents
              </Link>
              .
            </p>
          )}
        </div>
      )}
    </>
  );
}

function CarteAgent({
  fiche,
  selectionnee,
  selectionner,
}: {
  fiche: AgentCatalogue;
  selectionnee: boolean;
  selectionner: () => void;
}) {
  return (
    <button
      type="button"
      onClick={selectionner}
      aria-current={selectionnee ? "true" : undefined}
      className={
        "rounded-md border px-3 py-2 text-left text-sm shadow-sm " +
        (selectionnee
          ? "border-neutral-400 bg-neutral-100 dark:border-neutral-600 dark:bg-neutral-800"
          : "border-neutral-200 bg-white hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-800")
      }
    >
      <span className="block font-medium">🤖 {fiche.nom}</span>
      <span className="mt-0.5 block text-xs text-neutral-500 dark:text-neutral-400">
        {fiche.role}
      </span>
    </button>
  );
}

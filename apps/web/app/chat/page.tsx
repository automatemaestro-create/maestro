"use client";

/**
 * La page Chat de la Control Tower (ticket #85, lot 2 de #82) : converser
 * avec chaque agent du catalogue. Branchée sur l'API du lot 1 (#84,
 * `/api/chat`) : l'historique persisté se recharge au retour sur la page,
 * les nouveaux messages arrivent en temps réel par le WebSocket.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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
    <main className="mx-auto flex w-full max-w-screen-2xl flex-1 flex-col gap-6 p-4 sm:p-6">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-neutral-200 pb-4 dark:border-neutral-800">
        <h1 className="text-xl font-semibold tracking-tight">
          💬 Maestro — Chat des agents
        </h1>
        <Link
          href="/"
          className="ml-auto text-sm text-neutral-600 hover:text-neutral-900 hover:underline dark:text-neutral-400 dark:hover:text-neutral-200"
        >
          ← Tableau de bord
        </Link>
      </header>
      {erreur && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          API injoignable : {erreur} — vérifier que le backend tourne (
          <code className="font-mono">maestro-api</code>).
        </p>
      )}
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
    </main>
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

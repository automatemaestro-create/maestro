"use client";

/**
 * La page Catalogue de la Control Tower (ticket #73, EF-03) : créer et
 * configurer entièrement un agent depuis l'UI. Branchée sur l'API du lot 1
 * (#72, `/api/catalogue`) : les agents par défaut du code y sont consultables,
 * les personnalisés s'y créent, s'y modifient et s'y suppriment — un agent
 * personnalisé est chargé par les moteurs construits ensuite.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { CreationAgent, EditeurAgent } from "@/components/EditeurAgent";
import { chargerCatalogue } from "@/lib/api";
import { AGENT_SOURCE_DEFAUT, type AgentCatalogue } from "@/lib/types";

/** Ce que montre le panneau de droite : une fiche, ou le formulaire de création. */
type Selection = { mode: "fiche"; nom: string } | { mode: "creation" };

export default function PageCatalogue() {
  const [fiches, setFiches] = useState<AgentCatalogue[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    try {
      const nouvelles = await chargerCatalogue();
      setFiches(nouvelles);
      // Le premier agent est sélectionné d'office ; une sélection existante
      // survit au rechargement (une fiche supprimée retombe sur le premier).
      setSelection((courante) =>
        courante !== null &&
        (courante.mode === "creation" ||
          nouvelles.some((f) => f.nom === courante.nom))
          ? courante
          : nouvelles[0] !== undefined
            ? { mode: "fiche", nom: nouvelles[0].nom }
            : null,
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
            <button
              type="button"
              onClick={() => setSelection({ mode: "creation" })}
              aria-current={selection?.mode === "creation" ? "true" : undefined}
              className={
                "rounded-md border border-dashed px-3 py-2 text-left text-sm font-medium shadow-sm " +
                (selection?.mode === "creation"
                  ? "border-emerald-500 bg-emerald-50 text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
                  : "border-neutral-300 bg-white text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800")
              }
            >
              ➕ Nouvel agent
            </button>
            {fiches.map((fiche) => (
              <CarteAgent
                key={fiche.nom}
                fiche={fiche}
                selectionnee={
                  selection?.mode === "fiche" && selection.nom === fiche.nom
                }
                selectionner={() =>
                  setSelection({ mode: "fiche", nom: fiche.nom })
                }
              />
            ))}
          </nav>
          {selection?.mode === "creation" && (
            <CreationAgent
              onCreation={async (nom) => {
                setSelection({ mode: "fiche", nom });
                await recharger();
              }}
            />
          )}
          {selection?.mode === "fiche" && (
            <EditeurAgent
              key={selection.nom}
              nom={selection.nom}
              onChangement={recharger}
            />
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
        {" · "}
        {fiche.source === AGENT_SOURCE_DEFAUT ? "du code" : "personnalisé"}
      </span>
    </button>
  );
}

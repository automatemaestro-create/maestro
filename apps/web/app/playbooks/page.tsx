"use client";

/**
 * La page Playbooks de la Control Tower (ticket #77) : consulter et éditer
 * les instructions versionnées de chaque agent (#76, docs/04 §1), avec
 * historique des versions et restauration d'une version antérieure
 * (EF-24/EF-25). Branchée sur l'API du lot 1 (`/api/playbooks`) ; une version
 * publiée ici est chargée par les moteurs construits ensuite — l'application
 * à chaud d'un moteur déjà en vie est le lot #78.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { EditeurPlaybook } from "@/components/EditeurPlaybook";
import { chargerPlaybooks } from "@/lib/api";
import { PLAYBOOK_SOURCE_DEFAUT, type PlaybookFiche } from "@/lib/types";

export default function PagePlaybooks() {
  const [fiches, setFiches] = useState<PlaybookFiche[]>([]);
  const [agentSelectionne, setAgentSelectionne] = useState<string | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    try {
      const nouvelles = await chargerPlaybooks();
      setFiches(nouvelles);
      // Le premier agent est sélectionné d'office ; une sélection existante
      // survit au rechargement (après publication, la liste change de versions).
      setAgentSelectionne((courant) =>
        courant !== null && nouvelles.some((f) => f.agent === courant)
          ? courant
          : (nouvelles[0]?.agent ?? null),
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
        <p className="text-sm text-neutral-500">Chargement des playbooks…</p>
      ) : (
        <div className="flex flex-col gap-6 lg:flex-row">
          <nav
            aria-label="Agents"
            className="flex flex-row flex-wrap content-start gap-2 lg:w-56 lg:shrink-0 lg:flex-col"
          >
            {fiches.map((fiche) => (
              <CarteAgent
                key={fiche.agent}
                fiche={fiche}
                selectionnee={fiche.agent === agentSelectionne}
                selectionner={() => setAgentSelectionne(fiche.agent)}
              />
            ))}
          </nav>
          {agentSelectionne !== null && (
            <EditeurPlaybook
              key={agentSelectionne}
              agent={agentSelectionne}
              onPublication={recharger}
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
  fiche: PlaybookFiche;
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
      <span className="block font-medium">🤖 {fiche.agent}</span>
      <span className="mt-0.5 block text-xs text-neutral-500 dark:text-neutral-400">
        {fiche.role}
        {" · "}
        {fiche.source === PLAYBOOK_SOURCE_DEFAUT
          ? "playbook du code"
          : `v${fiche.version}`}
      </span>
    </button>
  );
}

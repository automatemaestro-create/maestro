"use client";

/**
 * La liste des agents (#190, lot 1 de #189) : le point d'entrée unique de
 * l'entrée de menu « Agents ». Chaque carte ouvre la fiche de l'agent, où ses
 * facettes tiennent en onglets — c'est ce qui remplace les trois sélecteurs
 * d'agent des anciennes pages Catalogue, Playbooks et Chat.
 *
 * `ongletCible` porte l'intention d'où l'on vient : une redirection depuis
 * `/playbooks` arrive ici avec `?onglet=playbook`, et les cartes visent alors
 * directement cet onglet — un signet sur l'ancienne page continue donc de
 * mener au bon endroit, sans détour par le profil.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { CreationAgent } from "@/components/EditeurAgent";
import { chargerCatalogue } from "@/lib/api";
import {
  type CleOngletAgent,
  cheminOnglet,
  ONGLET_AGENT_DEFAUT,
  ONGLETS_AGENT,
} from "@/lib/agents";
import { AGENT_SOURCE_DEFAUT, type AgentCatalogue } from "@/lib/types";

export function ListeAgents({
  ongletCible = ONGLET_AGENT_DEFAUT,
}: {
  ongletCible?: CleOngletAgent;
}) {
  const router = useRouter();
  const [fiches, setFiches] = useState<AgentCatalogue[]>([]);
  const [creation, setCreation] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    try {
      setFiches(await chargerCatalogue());
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

  const libelleCible = ONGLETS_AGENT.find(
    (onglet) => onglet.cle === ongletCible,
  );

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
          🧩 Agents
        </h2>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {ongletCible === ONGLET_AGENT_DEFAUT
            ? "Un agent, une fiche : profil, playbook, MCP & permissions, chat."
            : `Ouvre l'onglet « ${libelleCible?.libelle} » de l'agent choisi.`}
        </span>
      </div>

      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement du catalogue…</p>
      ) : (
        <>
          <ul className="grid gap-3 @md:grid-cols-2 @3xl:grid-cols-3">
            {fiches.map((fiche) => (
              <li key={fiche.nom}>
                <CarteAgent fiche={fiche} onglet={ongletCible} />
              </li>
            ))}
            {fiches.length === 0 && (
              <li className="text-sm text-neutral-500">
                Aucun agent au catalogue — en créer un ci-dessous.
              </li>
            )}
          </ul>

          {creation ? (
            <CreationAgent
              onCreation={(nom) => router.push(cheminOnglet(nom))}
            />
          ) : (
            <div>
              <button
                type="button"
                onClick={() => setCreation(true)}
                className="rounded-md border border-dashed border-neutral-300 px-3 py-2 text-sm font-medium text-neutral-600 shadow-sm hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
              >
                ➕ Nouvel agent
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}

function CarteAgent({
  fiche,
  onglet,
}: {
  fiche: AgentCatalogue;
  onglet: CleOngletAgent;
}) {
  return (
    <Link
      href={cheminOnglet(fiche.nom, onglet)}
      className="block h-full rounded-md border border-neutral-200 bg-white px-3 py-2 text-left text-sm shadow-sm hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:bg-neutral-800"
    >
      <span className="block font-medium">🤖 {fiche.nom}</span>
      <span className="mt-0.5 block text-xs text-neutral-500 dark:text-neutral-400">
        {fiche.role}
        {" · "}
        {fiche.source === AGENT_SOURCE_DEFAUT ? "du code" : "personnalisé"}
      </span>
    </Link>
  );
}

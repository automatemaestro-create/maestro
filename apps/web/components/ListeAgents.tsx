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
import { IconeAgent, IconeAgents, IconePlus } from "@/components/Icones";
import { classesCarte, EnTeteSection } from "@/components/Primitives";
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
      <EnTeteSection
        titre="Agents"
        icone={IconeAgents}
        className="justify-start"
        aside={
          <span className="text-annexe text-neutral-500 dark:text-neutral-400">
            {ongletCible === ONGLET_AGENT_DEFAUT
              ? "Un agent, une fiche : profil, playbook, MCP & permissions, chat."
              : `Ouvre l'onglet « ${libelleCible?.libelle} » de l'agent choisi.`}
          </span>
        }
      />

      {chargement ? (
        <p className="text-corps text-neutral-500">Chargement du catalogue…</p>
      ) : (
        <>
          <ul className="grid gap-3 @md:grid-cols-2 @3xl:grid-cols-3">
            {fiches.map((fiche) => (
              <li key={fiche.nom}>
                <CarteAgent fiche={fiche} onglet={ongletCible} />
              </li>
            ))}
            {fiches.length === 0 && (
              <li className="text-corps text-neutral-500">
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
                className="inline-flex items-center gap-1.5 rounded-lg border border-dashed border-neutral-300 px-3 py-2 text-corps font-medium text-neutral-600 shadow-sm hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
              >
                <IconePlus className="size-4 shrink-0" />
                Nouvel agent
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
      // La surface vient de la primitive ; ce qui reste ici est ce qu'un lien
      // ajoute — sa hauteur dans la grille, son alignement, son survol.
      className={classesCarte({
        densite: "aucune",
        className:
          "block h-full px-3 py-2 text-left text-corps hover:bg-neutral-50 dark:hover:bg-neutral-800",
      })}
    >
      <span className="flex items-center gap-1.5 font-medium">
        <IconeAgent className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
        {fiche.nom}
      </span>
      <span className="mt-0.5 block text-annexe text-neutral-500 dark:text-neutral-400">
        {fiche.role}
        {" · "}
        {fiche.source === AGENT_SOURCE_DEFAUT ? "du code" : "personnalisé"}
      </span>
    </Link>
  );
}

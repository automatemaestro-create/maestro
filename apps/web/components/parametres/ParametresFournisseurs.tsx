"use client";

/**
 * Section « Fournisseurs & modèles » des Paramètres (#121) : ce que chaque agent
 * du catalogue consomme, lu sur `GET /api/catalogue` (#72).
 *
 * En lecture ici, à dessein : l'édition d'un agent (rôle, compétences, playbook,
 * fournisseur, modèle) est un formulaire entier, déjà livré par la page
 * Catalogue (#73) — dupliquer le champ ici ouvrirait deux chemins d'écriture
 * pour la même valeur. La section renvoie donc vers cette page.
 *
 * Un agent sans fournisseur ni modèle propre suit ceux de l'exécution
 * (`MAESTRO_PROVIDER` / `MAESTRO_MODEL`, côté backend) : c'est le cas par
 * défaut, pas un trou de configuration.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { chargerCatalogue } from "@/lib/api";
import type { AgentCatalogue } from "@/lib/types";

import { EtatVide } from "./SectionParametres";

export function ParametresFournisseurs() {
  const [fiches, setFiches] = useState<AgentCatalogue[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  // Chargement différé d'un tick (même mécanique que les autres pages) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          setFiches(await chargerCatalogue());
          setErreur(null);
        } catch (e) {
          setErreur(e instanceof Error ? e.message : String(e));
        } finally {
          setChargement(false);
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, []);

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement du catalogue…</p>;
  }
  if (erreur !== null || fiches.length === 0) {
    return (
      <EtatVide
        message={
          erreur !== null
            ? `Catalogue illisible : ${erreur}`
            : "Aucun agent au catalogue."
        }
        releve="Le fournisseur et le modèle par défaut de l'exécution viennent de MAESTRO_PROVIDER et MAESTRO_MODEL, côté backend."
        lien={{ href: "/agents", libelle: "Ouvrir la liste des agents" }}
      />
    );
  }

  return (
    <>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-neutral-200 text-left text-xs text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 font-medium">Fournisseur</th>
              <th className="py-1 pr-3 font-medium">Modèle</th>
              <th className="py-1 font-medium">Provenance</th>
            </tr>
          </thead>
          <tbody>
            {fiches.map((fiche) => (
              <tr
                key={fiche.nom}
                className="border-b border-neutral-100 last:border-b-0 dark:border-neutral-800/60"
              >
                <td className="py-2 pr-3 font-medium">{fiche.nom}</td>
                <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-300">
                  {fiche.fournisseur ?? <HeriteDeLExecution />}
                </td>
                <td className="py-2 pr-3 font-mono text-xs text-neutral-600 dark:text-neutral-300">
                  {fiche.modele ?? <HeriteDeLExecution />}
                </td>
                <td className="py-2 text-neutral-500 dark:text-neutral-400">
                  {fiche.source}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
        « Hérité » : l&apos;agent suit le fournisseur et le modèle de
        l&apos;exécution (<code className="font-mono">MAESTRO_PROVIDER</code> /{" "}
        <code className="font-mono">MAESTRO_MODEL</code>). Le choix par agent
        s&apos;édite sur l&apos;onglet Profil de la{" "}
        <Link
          href="/agents"
          className="font-medium text-emerald-700 underline underline-offset-2 hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300"
        >
          fiche de l&apos;agent
        </Link>
        .
      </p>
    </>
  );
}

function HeriteDeLExecution() {
  return (
    <span className="text-neutral-400 italic dark:text-neutral-500">Hérité</span>
  );
}

"use client";

/**
 * Section « Fournisseurs & modèles » des Paramètres (#121) : ce que chaque agent
 * du catalogue consomme, lu sur `GET /api/catalogue` (#72).
 *
 * En lecture ici, à dessein : l'édition d'un agent (rôle, compétences,
 * fournisseur, modèle, effort) est un formulaire entier, livré par l'**onglet
 * Profil** de sa fiche (`/agents/<nom>/profil`, #190) — dupliquer le champ ici
 * ouvrirait deux chemins d'écriture pour la même valeur. La section renvoie donc
 * vers cette fiche, et le playbook n'est plus de la partie : il a son propre
 * onglet depuis #259.
 *
 * ⚠ **Trois réglages et non deux** (#253) : l'effort a rejoint le fournisseur et
 * le modèle, et il atteint l'exécution. Une vue qui prétend résumer ce que chaque
 * agent consomme et en tait un tiers est une vue fausse — d'où sa colonne.
 *
 * ⚠ **Deux héritages qui ne se confondent pas** (#259). Un réglage absent partout
 * suit l'**exécution** (`MAESTRO_PROVIDER` / `MAESTRO_MODEL`, côté backend) ; un
 * réglage d'agent du code non surchargé suit le **code**, qui en déclare un. Les
 * dire d'un même mot ferait chercher dans le `.env` ce qui est écrit dans
 * `maestro/agents/catalog.py`. L'API tranche (`herite`, `reglages_du_code`), la
 * vue ne redéduit rien : une valeur affichée peut venir du code **ou** avoir été
 * surchargée à l'identique, et seule la première est héritée.
 */

import Link from "next/link";
import { useEffect, useState } from "react";

import { chargerCatalogue } from "@/lib/api";
import { libelleOrigine } from "@/lib/vueAgents";
import type { AgentCatalogue } from "@/lib/types";

import { EtatVide } from "./SectionParametres";

/**
 * Les trois réglages de modèle (#253), dans l'ordre où ils se lisent : on choisit
 * un fournisseur, puis un de ses modèles, puis l'effort que ce modèle admet.
 * Miroir de `REGLAGES_SURCHARGEABLES` (`maestro/agents/store.py`).
 */
type Reglage = "fournisseur" | "modele" | "effort";

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
              <th className="py-1 pr-3 font-medium">Effort</th>
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
                  <Valeur fiche={fiche} reglage="fournisseur" />
                </td>
                <td className="py-2 pr-3 font-mono text-xs text-neutral-600 dark:text-neutral-300">
                  <Valeur fiche={fiche} reglage="modele" />
                </td>
                <td className="py-2 pr-3 text-neutral-600 dark:text-neutral-300">
                  <Valeur fiche={fiche} reglage="effort" />
                </td>
                <td className="py-2 text-neutral-500 dark:text-neutral-400">
                  {libelleOrigine(fiche.source)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
        « Hérité » : l&apos;agent suit le fournisseur et le modèle de
        l&apos;exécution (<code className="font-mono">MAESTRO_PROVIDER</code> /{" "}
        <code className="font-mono">MAESTRO_MODEL</code>). « Du code » : le
        réglage vient de la définition livrée et la suit, tant qu&apos;on ne
        l&apos;a pas surchargé. Le choix par agent s&apos;édite sur l&apos;onglet
        Profil de la{" "}
        <Link
          href="/agents"
          className="font-medium text-emerald-700 underline underline-offset-2 hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300"
        >
          fiche de l&apos;agent
        </Link>
        , où il se pose sans dupliquer l&apos;agent.
      </p>
    </>
  );
}

/**
 * La valeur d'un réglage, et **d'où elle vient** (#253, #259).
 *
 * Trois rendus pour trois faits distincts : la valeur seule (posée sur l'agent),
 * la valeur marquée « du code » (héritée d'une définition livrée qui en déclare
 * une), et « Hérité » (rien nulle part — l'exécution décide). Le marqueur se lit
 * sur `herite`, que le serveur calcule : le redéduire en comparant la valeur à
 * `reglages_du_code` rendrait indiscernables « suit le code » et « surchargé à
 * l'identique », et l'écran mentirait sur ce qui suivra une évolution du code.
 */
function Valeur({ fiche, reglage }: { fiche: AgentCatalogue; reglage: Reglage }) {
  const valeur = fiche[reglage];
  if (valeur === null) return <HeriteDeLExecution />;
  if (!fiche.herite.includes(reglage)) return <>{valeur}</>;
  return (
    <>
      {valeur}{" "}
      <span className="text-xs text-neutral-400 italic dark:text-neutral-500">
        du code
      </span>
    </>
  );
}

function HeriteDeLExecution() {
  return (
    <span className="text-neutral-400 italic dark:text-neutral-500">Hérité</span>
  );
}

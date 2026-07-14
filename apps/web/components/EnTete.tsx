/**
 * Barre d'en-tête du tableau de bord : identité, état de la connexion temps
 * réel, accès au catalogue d'agents (#73), aux playbooks (#77) et au chat des
 * agents (#85), coût cumulé (docs/05 §2.1 — « lisibilité du coût »).
 */

import Link from "next/link";

import { formatCout } from "@/lib/format";

type Props = {
  /** WebSocket ouverte : l'UI reçoit les événements en direct. */
  connecte: boolean;
  /** Coût cumulé rapporté par les agents (null si rien n'a été rapporté). */
  coutTotal: number | null;
};

export function EnTete({ connecte, coutTotal }: Props) {
  return (
    <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-neutral-200 pb-4 dark:border-neutral-800">
      <h1 className="text-xl font-semibold tracking-tight">
        🎼 Maestro — Control Tower
      </h1>
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
            (connecte ? "bg-emerald-500" : "animate-pulse bg-amber-500")
          }
        />
        {connecte ? "Temps réel connecté" : "Reconnexion…"}
      </span>
      <Link
        href="/catalogue"
        className="ml-auto text-sm text-neutral-600 hover:text-neutral-900 hover:underline dark:text-neutral-400 dark:hover:text-neutral-200"
      >
        🧩 Agents
      </Link>
      <Link
        href="/playbooks"
        className="text-sm text-neutral-600 hover:text-neutral-900 hover:underline dark:text-neutral-400 dark:hover:text-neutral-200"
      >
        📖 Playbooks
      </Link>
      <Link
        href="/chat"
        className="text-sm text-neutral-600 hover:text-neutral-900 hover:underline dark:text-neutral-400 dark:hover:text-neutral-200"
      >
        💬 Chat
      </Link>
      <span className="text-sm text-neutral-600 dark:text-neutral-400">
        💰 Coût cumulé : <span className="font-medium">{formatCout(coutTotal)}</span>
      </span>
    </header>
  );
}

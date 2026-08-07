/**
 * La page Chat de la Control Tower — le chat **global**, non lié à un agent.
 *
 * Le chat *par agent* (#85) a rejoint la fiche agent en onglet (#190) : c'était
 * la même intention que « Agents » vue par une autre facette, et le menu n'en
 * garde qu'une par intention. Le chat global, lui, en est une distincte —
 * s'adresser à l'orchestration plutôt qu'à un exécutant. Il est traité par le
 * chantier « Chat » de la Phase 6 ; l'entrée de menu et cette route lui gardent
 * leur place, sur le modèle des emplacements réservés de la barre supérieure
 * (#117) : annoncés et inertes plutôt qu'absents.
 *
 * `/chat/<agent>`, lui, ne mène plus ici : `next.config.ts` le redirige vers
 * l'onglet Chat de l'agent — aucun signet ne casse.
 */

import Link from "next/link";

import { IconeAgents, IconeChat } from "@/components/Icones";
import { EnTeteSection } from "@/components/Primitives";
import { AGENT_ASSISTANCE } from "@/lib/assistance";

export default function PageChat() {
  return (
    <section aria-label="Chat global" className="flex flex-col gap-3">
      <EnTeteSection titre="Chat global" icone={IconeChat} />
      <p className="max-w-2xl text-corps text-neutral-600 dark:text-neutral-400">
        Un fil unique pour parler à l&apos;orchestration — poser une demande sans
        choisir d&apos;exécutant, suivre ce qui en découle. Il arrive avec le
        chantier « Chat » de la Phase 6.
      </p>
      <p className="max-w-2xl text-corps text-neutral-600 dark:text-neutral-400">
        En attendant, converser <strong>avec un agent</strong> se fait depuis sa
        fiche, onglet Chat :{" "}
        <Link
          href="/agents?onglet=chat"
          className="inline-flex items-center gap-1 font-medium text-neutral-900 underline dark:text-neutral-200"
        >
          <IconeAgents className="size-3.5 shrink-0" />
          Agents
        </Link>
        . Et le panneau d&apos;assistance (le bouton flottant, fil «{" "}
        {AGENT_ASSISTANCE} ») répond dès maintenant aux questions sur la Control
        Tower elle-même.
      </p>
    </section>
  );
}

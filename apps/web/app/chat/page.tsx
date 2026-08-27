/**
 * La page Chat de la Control Tower — le chat **global**, non lié à un agent.
 *
 * Le chat *par agent* (#85) a rejoint la fiche agent en onglet (#190) : c'était
 * la même intention que « Agents » vue par une autre facette, et le menu n'en
 * garde qu'une par intention. Le chat global, lui, en est une distincte —
 * s'adresser à l'orchestration plutôt qu'à un exécutant.
 *
 * **Depuis #483 cette page n'est plus inerte : le cadrage s'y décide.** Le brief
 * (#320), ses questions de clarification (#321) et la décision qui les clôt ont
 * quitté `/brief` pour la conversation — arbitrage du 2026-08-24 (revue #470,
 * docs/29 §4). Un point de contrôle ne vaut que s'il est **lu**, et une décision
 * se lit mieux là où on a la conversation qui l'a produite que dans un écran
 * qu'il faut aller ouvrir. C'est un déménagement : la décision **D5** de #218
 * tient, rien n'est décomposé avant validation humaine, et les deux routes de
 * §6.10 sont celles d'avant.
 *
 * Ce qui reste à venir est le fil de **messages** avec l'orchestration — poser
 * une demande sans choisir d'exécutant, suivre ce qui en découle : c'est le
 * chantier #268/#269, que ce lot prolonge sans le doubler. D'où une page qui
 * porte le cadrage aujourd'hui et le dit, plutôt qu'un texte d'attente.
 *
 * `/chat/<agent>`, lui, ne mène plus ici : `next.config.ts` le redirige vers
 * l'onglet Chat de l'agent — aucun signet ne casse.
 */

import Link from "next/link";

import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { IconeAgents, IconeChat } from "@/components/Icones";
import { EnTeteSection } from "@/components/Primitives";
import { AGENT_ASSISTANCE } from "@/lib/assistance";

export default function PageChat() {
  return (
    <section aria-label="Chat global" className="flex flex-col gap-3">
      <EnTeteSection titre="Chat global" icone={IconeChat} />
      <p className="max-w-2xl text-corps text-neutral-600 dark:text-neutral-400">
        C&apos;est ici que le <strong>cadrage d&apos;un run se décide</strong> :
        le brief se relit et se corrige dans le fil, les questions du Chef de
        projet s&apos;y répondent, et l&apos;accord — ou le refus — s&apos;y
        donne. Rien n&apos;est décomposé avant.
      </p>
      <FilDeCadrage />
      <p className="max-w-2xl text-corps text-neutral-600 dark:text-neutral-400">
        Le fil de messages avec l&apos;orchestration — poser une demande sans
        choisir d&apos;exécutant — existe côté API depuis #268 (canal{" "}
        <code>orchestrateur</code>) ; son écran arrive avec #269. En attendant,
        converser <strong>avec un agent</strong> se fait depuis sa fiche, onglet
        Chat :{" "}
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

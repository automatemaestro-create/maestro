/**
 * La page « Agents » de la Control Tower (#190, lot 1 de #189) : la liste des
 * agents, seul point d'entrée du menu vers eux.
 *
 * Composant serveur : `?onglet=` se lit dans `searchParams` plutôt que par
 * `useSearchParams`, ce qui évite d'avoir à ceinturer la liste d'un `Suspense`
 * pour le rendu statique. Il porte l'intention d'origine d'une redirection
 * (`/playbooks` → `?onglet=playbook`) ; une valeur inconnue retombe sur le
 * profil (`ongletAgentOuDefaut`).
 */

import { ListeAgents } from "@/components/ListeAgents";
import { ongletAgentOuDefaut } from "@/lib/agents";

export default async function PageAgents({
  searchParams,
}: {
  searchParams: Promise<{ [cle: string]: string | string[] | undefined }>;
}) {
  const { onglet } = await searchParams;
  return <ListeAgents ongletCible={ongletAgentOuDefaut(onglet)} />;
}

/**
 * Un onglet de la fiche agent (#190, lot 1 de #189) : profil, playbook,
 * MCP & permissions ou chat.
 *
 * Un segment dynamique plutôt que quatre routes jumelles : la liste des
 * onglets vit dans `lib/agents`, source unique de la barre d'onglets comme des
 * chemins. Un onglet inconnu est un 404 franc (`notFound`) — mieux qu'une page
 * vide, et le `?onglet=` des redirections, lui, tolère l'inconnu en retombant
 * sur le profil.
 */

import { notFound } from "next/navigation";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { estOngletAgent } from "@/lib/agents";

export default async function PageOngletAgent({
  params,
}: {
  params: Promise<{ nom: string; onglet: string }>;
}) {
  const { nom, onglet } = await params;
  if (!estOngletAgent(onglet)) notFound();
  return (
    <ContenuOngletAgent nom={decodeURIComponent(nom)} onglet={onglet} />
  );
}

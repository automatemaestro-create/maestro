"use client";

/**
 * Le contenu de l'onglet ouvert sur une fiche agent (#190, lot 1 de #189).
 *
 * Rien n'est réécrit ici : chaque onglet remonte le composant qui portait déjà
 * la facette — `EditeurAgent` (ex-page Catalogue), `EditeurPlaybook` (ex-page
 * Playbooks), `FilChat` (ex-page Chat) et les sections MCP/permissions, qui
 * n'avaient jusqu'ici de place que tout en bas de la fiche du catalogue. Ce
 * fichier ne fait que les aiguiller et leur donner ce qu'une page leur donnait
 * (la navigation après suppression, notamment).
 *
 * `OngletLogs` (#266) est le seul à ne reprendre aucune page : ce qu'un agent
 * faisait ne se lisait nulle part à son échelle. Il suit néanmoins la même règle
 * — le composant vit dans `components/`, cet aiguillage ne fait que le nommer.
 */

import { useRouter } from "next/navigation";

import {
  EditeurAgent,
  McpEtPermissionsAgent,
} from "@/components/EditeurAgent";
import { EditeurPlaybook } from "@/components/EditeurPlaybook";
import { FilChat } from "@/components/FilChat";
import { OngletLogs } from "@/components/OngletLogs";
import type { CleOngletAgent } from "@/lib/agents";

export function ContenuOngletAgent({
  nom,
  onglet,
}: {
  nom: string;
  onglet: CleOngletAgent;
}) {
  switch (onglet) {
    case "profil":
      return <OngletProfil nom={nom} />;
    case "playbook":
      return <EditeurPlaybook agent={nom} />;
    case "mcp":
      return <McpEtPermissionsAgent nom={nom} />;
    case "chat":
      return <FilChat agent={nom} />;
    case "logs":
      return <OngletLogs nom={nom} />;
  }
}

/**
 * Le profil, seul onglet à pouvoir faire disparaître son propre objet : un
 * agent supprimé n'a plus de fiche, la navigation revient donc à la liste.
 */
function OngletProfil({ nom }: { nom: string }) {
  const router = useRouter();
  return (
    <EditeurAgent nom={nom} onSuppression={() => router.push("/agents")} />
  );
}

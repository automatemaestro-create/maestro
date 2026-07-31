/**
 * Le cadre d'une fiche agent (#190, lot 1 de #189) : en-tête et barre
 * d'onglets, communs aux quatre facettes.
 *
 * En *layout* et non dans chaque page : la barre survit ainsi au passage d'un
 * onglet à l'autre — seul le contenu est remonté.
 */

import type { ReactNode } from "react";

import { OngletsAgent } from "@/components/OngletsAgent";

export default async function LayoutFicheAgent({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ nom: string }>;
}) {
  const { nom } = await params;
  return <OngletsAgent nom={decodeURIComponent(nom)}>{children}</OngletsAgent>;
}

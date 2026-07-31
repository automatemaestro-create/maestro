/**
 * `/agents/<nom>` sans onglet (#190) : la fiche s'ouvre sur son profil.
 *
 * Une redirection plutôt qu'un rendu du profil à cette adresse : la barre
 * d'onglets déduit l'onglet actif du chemin, donc chaque facette doit avoir
 * une URL et une seule — partageable, et marquée correctement au retour.
 */

import { redirect } from "next/navigation";

import { cheminOnglet } from "@/lib/agents";

export default async function PageFicheAgent({
  params,
}: {
  params: Promise<{ nom: string }>;
}) {
  const { nom } = await params;
  redirect(cheminOnglet(decodeURIComponent(nom)));
}

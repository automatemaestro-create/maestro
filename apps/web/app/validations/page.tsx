/**
 * La page Validations (#117, lot 1 de #116) : `/validations`.
 *
 * Une coquille comme les autres depuis #1228 — le contenu vit dans
 * `components/EcranValidations`, parce que c'est lui qui se teste.
 *
 * Composant serveur : `?retour=` se lit dans `searchParams` plutôt que par
 * `useSearchParams`, ce qui évite d'avoir à ceinturer l'écran d'un `Suspense`
 * pour le rendu statique — même partage que `/agents` et son `?onglet=` (#190).
 * Il porte **d'où l'on vient** quand un run a laissé partir ici (critère 2 de
 * #1228) ; `cheminDeRetour` refuse en silence tout ce qui n'est pas un chemin
 * interne, un paramètre d'URL étant la seule entrée que personne du produit n'a
 * écrite.
 */

import { EcranValidations } from "@/components/EcranValidations";
import { cheminDeRetour } from "@/lib/navigation";

export default async function PageValidations({
  searchParams,
}: {
  searchParams: Promise<{ [cle: string]: string | string[] | undefined }>;
}) {
  const { retour } = await searchParams;
  return <EcranValidations retour={cheminDeRetour(retour)} />;
}

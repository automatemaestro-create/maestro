/**
 * La page « Nouvel agent » (#254, lot 2 de #243) : `/agents/nouveau`.
 *
 * Une coquille comme les autres — le contenu vit dans
 * `components/CreationAgentEcran`, parce que c'est lui qui se teste. Elle ne
 * porte ni titre ni en-tête : la barre supérieure les dérive du menu (#117), et
 * une entrée couvre ses sous-chemins (`entreeCourante`), si bien que cette page
 * est titrée « Agents » sans avoir d'entrée à elle — c'est exactement ce que le
 * ticket demande, le cadre reste en place et seul le contenu change.
 *
 * ⚠ Segment **statique** voisin du segment dynamique `[nom]` : Next sert celui-ci
 * en premier, donc `/agents/nouveau` est la création et jamais la fiche d'un
 * agent qui porterait ce nom. L'ambiguïté est fermée du côté où elle naît —
 * `estNomAgentReserve` (`lib/agents`) refuse le nom à la saisie.
 */

import { CreationAgentEcran } from "@/components/CreationAgentEcran";

export default function PageCreationAgent() {
  return <CreationAgentEcran />;
}

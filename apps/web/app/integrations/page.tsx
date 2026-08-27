"use client";

/**
 * La page « Intégrations » (#270, docs/05 §2.8) : le pool projet et la
 * bibliothèque curée des serveurs MCP.
 *
 * Une coquille, comme « Runs », « Valider le brief » et « Composer un
 * objectif » : le contenu vit dans `components/integrations/`, parce que c'est
 * lui qui se teste. La page ne porte ni titre ni en-tête — la barre supérieure
 * les dérive du menu (#117).
 */

import { EcranIntegrations } from "@/components/integrations/EcranIntegrations";

export default function PageIntegrations() {
  return <EcranIntegrations />;
}

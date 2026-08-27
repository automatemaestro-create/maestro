"use client";

/**
 * Le service après-vente de l'ancre `#mcp` (#270).
 *
 * « Intégrations MCP » a été la cinquième section des Paramètres de #133 à
 * #270 ; elle est désormais un écran (`/integrations`). Restent les signets,
 * les liens de doc et les liens internes qui pointent `/parametres#mcp` : ils
 * doivent tomber sur l'écran, pas sur une page où la section n'est plus.
 *
 * ⚠ **Ce n'est pas une redirection `next.config.ts`, et ça ne peut pas l'être.**
 * Le fragment d'une URL n'est **jamais envoyé au serveur** — `redirects()` ne
 * voit que `/parametres` et ne saurait pas distinguer `#mcp` de `#apparence`.
 * Le test de `agents.test.tsx` le dit d'ailleurs par un autre bout : il résout
 * chaque `destination` en dossiers sous `app/`, et un chemin porteur de `#`
 * n'en désigne aucun. La redirection est donc **du client**, et son prix est
 * écrit ici : sans JavaScript, `/parametres#mcp` rend les Paramètres sans faire
 * défiler nulle part — l'ancre n'existe plus, mais la page reste servie.
 *
 * `replace` et non `push` : un signet ne doit pas laisser derrière lui une
 * entrée d'historique qui, au retour arrière, redirige à nouveau — c'est une
 * page dont on ne pourrait plus sortir par le bouton « Précédent ».
 *
 * La cible est **résolue par le menu** et jamais écrite en dur : le jour où
 * l'écran déménage, ce renvoi suit (même mécanique que les renvois du tableau
 * de bord, `entreeParLibelle`).
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { entreeParLibelle } from "@/lib/navigation";

/** L'ancre retirée par #270 — celle qu'on rattrape, et la seule. */
export const ANCRE_MCP = "#mcp";

export function RedirectionAncreMcp() {
  const router = useRouter();

  useEffect(() => {
    const cible = entreeParLibelle("Intégrations")?.href;
    if (cible === undefined) return;
    const rattraper = () => {
      if (window.location.hash === ANCRE_MCP) router.replace(cible);
    };
    // À l'arrivée (le signet) **et** au changement d'ancre : un lien `#mcp`
    // cliqué depuis la page elle-même ne remonte pas la page, il ne change que
    // le fragment — sans cet écouteur, il ne mènerait nulle part.
    rattraper();
    window.addEventListener("hashchange", rattraper);
    return () => window.removeEventListener("hashchange", rattraper);
  }, [router]);

  return null;
}

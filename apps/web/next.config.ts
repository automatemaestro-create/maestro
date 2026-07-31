import type { NextConfig } from "next";

/**
 * Les anciens chemins de la navigation v1 (#190, lot 1 de #189).
 *
 * `/catalogue`, `/playbooks` et `/chat/<agent>` regardaient trois facettes d'un
 * même objet ; elles sont devenues les onglets d'une seule fiche agent. Ces
 * redirections sont le contrat de non-régression du lot : aucun signet, aucun
 * lien déjà écrit — dans la doc, dans un ticket, dans un fil de discussion — ne
 * casse, et chacun retombe sur **l'onglet** qu'il visait.
 *
 * Sans agent dans l'URL (`/playbooks` nu), il n'y a pas de fiche à ouvrir : la
 * redirection mène à la liste en lui passant l'intention (`?onglet=playbook`),
 * dont les cartes visent alors directement cet onglet.
 *
 * `permanent: false` (307) et non 308 : un 308 est mis en cache par le
 * navigateur pour de bon, ce qui rendrait toute évolution ultérieure de ces
 * chemins — le chantier « Chat » de la Phase 6 en particulier — impossible à
 * corriger côté serveur. La garantie de durée vit dans ce fichier, pas dans le
 * cache des postes.
 */
export const REDIRECTIONS_NAVIGATION_V1 = [
  { source: "/catalogue", destination: "/agents", permanent: false },
  {
    source: "/catalogue/:nom",
    destination: "/agents/:nom/profil",
    permanent: false,
  },
  {
    source: "/playbooks",
    destination: "/agents?onglet=playbook",
    permanent: false,
  },
  {
    source: "/playbooks/:nom",
    destination: "/agents/:nom/playbook",
    permanent: false,
  },
  // `/chat` nu n'est PAS redirigé : il reste la page du chat global (Phase 6).
  { source: "/chat/:nom", destination: "/agents/:nom/chat", permanent: false },
];

const nextConfig: NextConfig = {
  redirects: async () => REDIRECTIONS_NAVIGATION_V1,
};

export default nextConfig;

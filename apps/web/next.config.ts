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

/**
 * Les deux écrans que le fil a absorbés (#484, lot 3 de #481).
 *
 * `/composer` (#319) et `/brief` (#322) ont quitté le menu le 2026-08-28 : ce
 * qu'ils savaient faire se fait dans la conversation depuis #482 (les pièces
 * jointes et les sources) et #483 (le cadrage et sa décision). Ils **restent
 * servis** — c'est le contrat de non-régression du lot, le même qu'en v1
 * ci-dessus : ces deux chemins sont écrits dans la doc, dans des tickets et dans
 * les signets de qui s'en servait tous les jours, et un 404 ne dit pas où le
 * geste est parti. Une redirection, si.
 *
 * `permanent: false` (307), pour la raison écrite au-dessus et qui vaut ici avec
 * plus de force encore : le fil est un chantier **en cours** (#481), et un 308
 * mis en cache par les navigateurs figerait sa destination avant qu'elle soit
 * stabilisée — la garantie de durée vit dans ce fichier, pas dans le cache des
 * postes.
 *
 * ⚠ Les dossiers `app/composer/` et `app/brief/` **restent en place**, et ce
 * n'est pas un oubli : une redirection de `next.config` est évaluée **avant** le
 * routage, donc elle l'emporte et personne n'atteint plus ces pages par leur
 * URL. Leurs composants, eux, sont toujours montés — par le fil du cadrage
 * (#483, qui rend `components/brief/` tel quel) et par les harnais de test. Les
 * supprimer est une décision à part, qui ne relève ni du menu ni des chemins.
 */
export const REDIRECTIONS_PORTE_UNIQUE = [
  { source: "/composer", destination: "/chat", permanent: false },
  { source: "/brief", destination: "/chat", permanent: false },
];

const nextConfig: NextConfig = {
  redirects: async () => [
    ...REDIRECTIONS_NAVIGATION_V1,
    ...REDIRECTIONS_PORTE_UNIQUE,
  ],
};

export default nextConfig;

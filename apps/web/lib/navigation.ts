/**
 * Le menu principal du backoffice (#117) : une seule liste, consommée par la
 * sidebar (entrées de navigation) et par la barre supérieure (titre de la page
 * courante). Ajouter une page se fait ici, pas dans deux composants.
 */

import type { ComponentType, SVGProps } from "react";

import {
  IconeAgents,
  IconeChat,
  IconeCouts,
  IconeJournal,
  IconeParametres,
  IconeProjets,
  IconeTableauDeBord,
  IconeValidations,
} from "@/components/Icones";

export type EntreeMenu = {
  href: string;
  /** Libellé du menu — aussi le titre affiché en barre supérieure. */
  libelle: string;
  Icone: ComponentType<SVGProps<SVGSVGElement>>;
};

/**
 * Une entrée par **intention** (#190) : « Agents » mène à la liste des agents,
 * d'où chaque fiche ouvre ses onglets (profil, playbook, MCP & permissions,
 * chat). Les anciennes entrées « Playbooks » et « Chat par agent » regardaient
 * le même objet par une autre facette — elles sont devenues des onglets, leurs
 * chemins restant servis par les redirections de `next.config.ts`.
 *
 * « Chat » subsiste et vise le chat **global**, non lié à un agent : c'est une
 * intention distincte, portée par le chantier « Chat » de la Phase 6.
 *
 * « Projets » (#225) vient juste après l'accueil parce qu'il porte le **cadre**
 * de tout le reste : un agent, un coût, une validation appartiennent à un
 * projet (Phase 7). Il précède donc les écrans qui s'y rapportent, plutôt que
 * de se ranger parmi les réglages — déclarer où Maestro travaille n'est pas un
 * paramètre du poste.
 *
 * « Journal » (#249) ferme le groupe de ce qu'on **observe** (coûts,
 * validations, journal), juste avant les réglages : le fil d'activité y tient
 * en plein format, là où le tableau de bord n'en garde qu'un aperçu. Son entrée
 * ici est ce qui **allume** le renvoi de cet aperçu — `entreeParLibelle`
 * (ci-dessous) le résout par le menu, `FilActivite` n'a rien à savoir du chemin.
 */
export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/projets", libelle: "Projets", Icone: IconeProjets },
  { href: "/agents", libelle: "Agents", Icone: IconeAgents },
  { href: "/chat", libelle: "Chat", Icone: IconeChat },
  { href: "/couts", libelle: "Coûts & analytics", Icone: IconeCouts },
  { href: "/validations", libelle: "Validations", Icone: IconeValidations },
  { href: "/journal", libelle: "Journal", Icone: IconeJournal },
  { href: "/parametres", libelle: "Paramètres", Icone: IconeParametres },
];

/**
 * L'entrée du menu qui porte le chemin courant. La racine ne matche qu'elle-même
 * (sinon elle serait active partout) ; les autres couvrent leurs sous-chemins.
 */
export function entreeCourante(chemin: string): EntreeMenu | undefined {
  return MENU.find((entree) =>
    entree.href === "/"
      ? chemin === "/"
      : chemin === entree.href || chemin.startsWith(`${entree.href}/`),
  );
}

/**
 * L'entrée du menu qui porte ce libellé, ou `undefined` si la page n'existe pas
 * (encore).
 *
 * Le tableau de bord épuré (#191) ne garde que l'essentiel et **renvoie** vers
 * la page de chaque panneau qu'il a rangé. Résoudre ces renvois par le menu
 * plutôt que par un chemin en dur donne deux propriétés : ils suivent d'eux-mêmes
 * une réorganisation — « Agents » change de chemin en #190 — et un renvoi vers
 * une page **pas encore créée** ne s'allume que le jour où elle entre au menu,
 * sans lien mort en attendant. Le Journal en a fait la démonstration : écrit
 * dans `FilActivite` dès #191, son renvoi est resté éteint jusqu'à ce que #249
 * ajoute l'entrée ci-dessus — sans une ligne de plus dans le composant.
 */
export function entreeParLibelle(libelle: string): EntreeMenu | undefined {
  return MENU.find((entree) => entree.libelle === libelle);
}

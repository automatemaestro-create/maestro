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
  IconeParametres,
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
 */
export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/agents", libelle: "Agents", Icone: IconeAgents },
  { href: "/chat", libelle: "Chat", Icone: IconeChat },
  { href: "/couts", libelle: "Coûts & analytics", Icone: IconeCouts },
  { href: "/validations", libelle: "Validations", Icone: IconeValidations },
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
 * une page **pas encore créée** (le Journal du chantier « Visibilité ») ne
 * s'allume que le jour où elle entre au menu, sans lien mort en attendant.
 */
export function entreeParLibelle(libelle: string): EntreeMenu | undefined {
  return MENU.find((entree) => entree.libelle === libelle);
}

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
  IconePlaybooks,
  IconeTableauDeBord,
  IconeValidations,
} from "@/components/Icones";

export type EntreeMenu = {
  href: string;
  /** Libellé du menu — aussi le titre affiché en barre supérieure. */
  libelle: string;
  Icone: ComponentType<SVGProps<SVGSVGElement>>;
};

export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/catalogue", libelle: "Agents", Icone: IconeAgents },
  { href: "/playbooks", libelle: "Playbooks", Icone: IconePlaybooks },
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

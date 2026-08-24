/**
 * Le menu principal du backoffice (#117) : une seule liste, consommée par la
 * sidebar (entrées de navigation) et par la barre supérieure (titre de la page
 * courante). Ajouter une page se fait ici, pas dans deux composants.
 */

import type { ComponentType, SVGProps } from "react";

import {
  IconeAgents,
  IconeBrief,
  IconeChat,
  IconeCouts,
  IconeJournal,
  IconeObjectif,
  IconeParametres,
  IconeProjets,
  IconeRuns,
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
 * « Projets » (#225) y a figuré un temps, juste après l'accueil ; elle n'y est
 * plus (#280) — voir `HORS_MENU`.
 *
 * « Journal » (#249) ferme le groupe de ce qu'on **observe** (coûts,
 * validations, journal), juste avant les réglages : le fil d'activité y tient
 * en plein format, là où le tableau de bord n'en garde qu'un aperçu. Son entrée
 * ici est ce qui **allume** le renvoi de cet aperçu — `entreeParLibelle`
 * (ci-dessous) le résout par le menu, `FilActivite` n'a rien à savoir du chemin.
 *
 * « Composer un objectif » (#319) ouvre la liste, juste après l'accueil, et **au
 * menu** plutôt que dans `HORS_MENU` : c'est le geste par lequel on entre dans
 * un run, pas une destination où l'on passe. L'y ranger n'est pas revenir sur
 * #280 — « Projets » en est sorti parce qu'un projet est le **cadre** des
 * écrans ; composer, c'est une action, et une action qu'on ne trouve pas est une
 * action qui n'existe pas (le tableau de bord vide renvoyait à `curl`).
 *
 * « Valider le brief » (#322) suit « Composer un objectif », dont elle est
 * l'autre moitié : on compose, le Chef de projet rédige, on tranche. **Au menu**
 * et non dans `HORS_MENU` bien qu'on y arrive le plus souvent par la cloche ou
 * par le tableau de bord — un run suspendu sur son brief ne crée aucune tâche,
 * donc rien d'autre ne le montre, et une destination qui n'apparaît que le jour
 * où quelque chose l'appelle est une destination qu'on ne pense pas à ouvrir. La
 * file y est vide la plupart du temps, et le dit : c'est le prix de savoir qu'on
 * n'a rien laissé en plan.
 *
 * « Runs » (#474, lot 2 de #472) **ferme ce groupe de tête** : on compose, le Chef
 * de projet rédige, on tranche — puis on regarde ce qui tourne. Elle est au menu
 * parce qu'un run n'était l'objet d'**aucun** écran : on y entrait par « Composer
 * un objectif » et on n'y revenait jamais, les runs passés n'étant listés nulle
 * part (revue #470, docs/29 §3). Sa place **avant** « Agents » suit le principe des
 * deux précédentes — le haut du menu porte le travail en cours, le bas les
 * ressources qui le servent et ce qu'on observe après coup.
 */
export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/composer", libelle: "Composer un objectif", Icone: IconeObjectif },
  { href: "/brief", libelle: "Valider le brief", Icone: IconeBrief },
  { href: "/runs", libelle: "Runs", Icone: IconeRuns },
  { href: "/agents", libelle: "Agents", Icone: IconeAgents },
  { href: "/chat", libelle: "Chat", Icone: IconeChat },
  { href: "/couts", libelle: "Coûts & analytics", Icone: IconeCouts },
  { href: "/validations", libelle: "Validations", Icone: IconeValidations },
  { href: "/journal", libelle: "Journal", Icone: IconeJournal },
  { href: "/parametres", libelle: "Paramètres", Icone: IconeParametres },
];

/**
 * Les pages **servies mais hors navigation** (#280) : elles ont un titre de
 * barre supérieure comme les autres, mais pas d'entrée dans la sidebar, parce
 * qu'on y arrive par un autre geste que le menu.
 *
 * « Projets » est la première, et c'est tout le lot 4 de #276 : le projet est le
 * **cadre** de toutes les destinations, pas l'une d'elles. Le ranger dans la
 * barre latérale en faisait une destination parmi d'autres — le reproche du
 * bilan de la Phase 7 — alors qu'on en change comme on change de contexte, au
 * **sélecteur du shell** (`components/projets/SelecteurProjet`), d'où cet écran
 * est désormais atteint.
 *
 * Deux propriétés à ne pas défaire :
 *
 * - **la page reste servie**, et à son chemin d'origine : rien à rediriger dans
 *   `next.config.ts` — contrairement aux pages fusionnées de #190, celle-ci n'a
 *   pas déménagé, elle a seulement quitté le menu. Un signet, un lien de doc ou
 *   un ticket qui pointe `/projets` tombe donc toujours sur l'écran de #225,
 *   inchangé ;
 * - **elle garde son titre** : sans cette liste, `entreeCourante` ne la
 *   reconnaîtrait plus et la barre supérieure retomberait sur « Control
 *   Tower » — un écran anonyme pour un chemin qui marche.
 */
export const HORS_MENU: EntreeMenu[] = [
  { href: "/projets", libelle: "Projets", Icone: IconeProjets },
];

/**
 * Toutes les pages que l'application titre, menu ou non. La résolution passe par
 * ici plutôt que par `MENU` seul : une page hors menu reste une page, elle a un
 * nom et un chemin — c'est seulement la sidebar qui l'ignore, en itérant `MENU`.
 */
const PAGES: EntreeMenu[] = [...MENU, ...HORS_MENU];

/**
 * L'entrée qui porte le chemin courant. La racine ne matche qu'elle-même
 * (sinon elle serait active partout) ; les autres couvrent leurs sous-chemins.
 */
export function entreeCourante(chemin: string): EntreeMenu | undefined {
  return PAGES.find((entree) =>
    entree.href === "/"
      ? chemin === "/"
      : chemin === entree.href || chemin.startsWith(`${entree.href}/`),
  );
}

/**
 * L'entrée qui porte ce libellé, ou `undefined` si la page n'existe pas
 * (encore). Les pages hors menu en font partie : « Projets » (#280) reste
 * résoluble par son nom, ce qui est ce qui permet au sélecteur de viser son
 * écran sans en écrire le chemin en dur.
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
  return PAGES.find((entree) => entree.libelle === libelle);
}

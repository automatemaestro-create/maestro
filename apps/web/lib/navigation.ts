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
 * « Chat » vise le chat **global**, non lié à un agent : c'est une intention
 * distincte, portée par le chantier « Chat » de la Phase 6. Depuis #484 elle
 * **ouvre le menu**, juste après l'accueil, à la place qu'occupaient les deux
 * entrées dont elle a absorbé le geste — c'est le déplacement qui donne son sens
 * au retrait : une porte d'entrée en quatrième position n'est pas une porte
 * d'entrée, et le tableau de bord vide y renvoie désormais (`PosteVide`).
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
 * ⚠ **« Composer un objectif » (#319) et « Valider le brief » (#322) sont
 * parties le 2026-08-28** (#484, lot 3 de #481), et c'est un **renversement**
 * assumé de la Phase 8, pas un rangement : les deux étaient ici en toutes lettres
 * — composer parce qu'« une action qu'on ne trouve pas est une action qui
 * n'existe pas », valider parce qu'« un run suspendu sur son brief ne crée aucune
 * tâche, donc rien d'autre ne le montre ». Les deux arguments **tiennent
 * toujours** ; ce qui a changé est leur conclusion. La revue d'usage du
 * 2026-08-24 (#470, [docs/29 §4]) a tranché que le fil serait la **seule** porte
 * d'entrée : #482 lui a donné les pièces jointes et les sources, #483 le cadrage
 * et sa décision. Les deux entrées ne portaient donc plus rien d'unique, et deux
 * portes vers un même geste sont la question « laquelle ? » posée à chaque
 * lancement.
 *
 * Ce n'est pas non plus #280 rejoué : « Projets » a quitté le menu **en gardant
 * son écran** (`HORS_MENU` ci-dessous), parce que le projet est le cadre des
 * destinations et non l'une d'elles. Ici l'écran ne reste pas une destination du
 * tout — `/composer` et `/brief` **redirigent** vers le fil (307,
 * `next.config.ts`) —, d'où leur absence des deux listes : les ranger dans
 * `HORS_MENU` leur donnerait un titre de barre supérieure qu'aucun rendu
 * n'atteindrait, et laisserait `entreeParLibelle` résoudre un libellé vers un
 * chemin qui se dérobe.
 *
 * Corollaire à ne pas défaire : **cinq** surfaces acheminaient vers ces deux
 * écrans, et toutes visent « Chat » avant que ce retrait ne prenne effet. Un
 * renvoi résolu par le menu (règle de #191) rend `undefined` quand son libellé
 * part, donc `null`, donc un bloc qui disparaît **sans un mot** — un run
 * suspendu que plus rien ne montre, c'est-à-dire le défaut même que l'argument
 * de #322 ci-dessus interdisait. Retirer une entrée de menu n'est jamais un
 * geste local.
 *
 * Elles ont bougé en **deux temps**, et l'ordre était le bon : les trois du
 * **cadrage** — le panneau du tableau de bord, la cloche, la table `ATTENTES` —
 * l'ont fait dans #483, qui les a rangées derrière `PAGE_DU_CADRAGE`
 * (`lib/brief`) *avant* que l'entrée parte ; les deux du **lancement** le font
 * ici, derrière `PAGE_DU_FIL` (ci-dessous). Preuve que la précaution valait :
 * #484 n'a eu à toucher **aucun** des trois fichiers du cadrage.
 *
 * « Runs » (#474, lot 2 de #472) **ferme ce groupe de tête** : on parle à
 * Maestro dans le fil, puis on regarde ce qui tourne. Elle est au menu parce
 * qu'un run n'était l'objet d'**aucun** écran : on y entrait par « Composer un
 * objectif » et on n'y revenait jamais, les runs passés n'étant listés nulle
 * part (revue #470, docs/29 §3). Le groupe a perdu deux entrées sur trois, le
 * principe de l'ordre ne bouge pas — le haut du menu porte le travail en cours,
 * le bas les ressources qui le servent et ce qu'on observe après coup.
 */
export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/chat", libelle: "Chat", Icone: IconeChat },
  { href: "/runs", libelle: "Runs", Icone: IconeRuns },
  { href: "/agents", libelle: "Agents", Icone: IconeAgents },
  { href: "/couts", libelle: "Coûts & analytics", Icone: IconeCouts },
  { href: "/validations", libelle: "Validations", Icone: IconeValidations },
  { href: "/journal", libelle: "Journal", Icone: IconeJournal },
  { href: "/parametres", libelle: "Paramètres", Icone: IconeParametres },
];

/**
 * Le **libellé de menu du fil** — la seule porte d'entrée depuis #484.
 *
 * Il existe pour la raison qui a fait exister `PAGE_DU_CADRAGE` (`lib/brief`, la
 * moitié de #483) : plusieurs surfaces **acheminent** vers cette page sans
 * décider, et elles doivent bouger ensemble ou pas du tout. Ici ce sont celles
 * qui nommaient le geste de **lancement** — le poste vide (`PosteVide`), la
 * liste de runs vide (`runs/ListeRuns`) et la file de briefs vide
 * (`brief/ValidationBriefs`) —, qui renvoyaient toutes à « Composer un
 * objectif ». Recopier le libellé dans les trois, c'est se donner trois chances
 * d'en oublier une, et une seule oubliée est un écran vide qui ne nomme plus
 * aucun geste : exactement ce que #186 avait corrigé en renvoyant à `curl`.
 *
 * C'est un **libellé** et non un chemin, comme partout depuis #191 : le renvoi
 * suit sa page si elle déménage, et ne s'allume pas vers une page absente.
 *
 * ⚠ Il vaut « Chat » comme `PAGE_DU_CADRAGE`, et les deux ne sont pas à fondre :
 * ils répondent à deux questions — « d'où lance-t-on ? » et « où tranche-t-on un
 * brief ? » — que le fil réunit **aujourd'hui**, ce qui est le sujet de #481, pas
 * une propriété acquise. Les fondre reviendrait à décréter qu'elles ne pourront
 * plus jamais diverger.
 */
export const PAGE_DU_FIL = "Chat";

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

/**
 * Le chemin de la **vue d'un run** (#475) — `/runs/<run_id>`, dérivé de l'entrée
 * « Runs » et jamais écrit en dur.
 *
 * Une page à segment dynamique n'a pas d'entrée à elle : elle vit **sous** celle
 * de sa liste, ce que `entreeCourante` sait déjà (une entrée couvre ses
 * sous-chemins, donc la barre supérieure titre « Runs » sur la vue d'un run). Il
 * manquait le sens inverse — fabriquer le chemin —, et le faire ici plutôt que
 * dans un composant donne la même propriété qu'`entreeParLibelle` : le jour où
 * « Runs » déménage, les renvois suivent, et un renvoi vers une page qui n'existe
 * pas encore ne s'allume pas (`undefined` plutôt qu'un lien mort).
 */
export function hrefRun(runId: string): string | undefined {
  const runs = entreeParLibelle("Runs");
  return runs && `${runs.href}/${encodeURIComponent(runId)}`;
}

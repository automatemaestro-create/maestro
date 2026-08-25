/**
 * Les deux **lectures** d'un run et la bascule entre elles (#491, lot 3 de #488)
 * — l'arbitrage que le ticket demandait de rendre, écrit ici et une seule fois.
 *
 * ## Ce qui est tranché
 *
 * **Les deux vues coexistent, sous une bascule, et le pipeline est le défaut.**
 * Elles ne se remplacent pas, parce qu'elles ne répondent pas à la même question
 * (docs/05 §6.11) : le Kanban dit **« combien dans quel état »**, le pipeline dit
 * **« quoi après quoi »**. Aucune des deux ne se déduit de l'autre — on ne lit
 * pas un enchaînement dans cinq colonnes, on ne compte pas un état dans un
 * graphe.
 *
 * **Mais elles ne s'affichent jamais ensemble**, et c'est le cœur de l'arbitrage :
 * empilées, elles se concurrenceraient — deux fois les mêmes tâches sur le même
 * écran, sans que rien ne dise laquelle regarder. C'est ce que #488 interdit en
 * toutes lettres (« jamais laissé aux deux écrans de se concurrencer »). Une
 * bascule à deux positions n'en montre qu'une, et le geste de passer de l'une à
 * l'autre *est* la question qu'on se pose.
 *
 * **Le pipeline ouvre.** Le Kanban d'un run a été livré quatre lots plus tôt
 * (#475) et c'était le bon ordre, mais sa question est déjà à moitié répondue
 * au-dessus de lui : la barre de progression de l'en-tête compte par
 * compartiment, avec les mêmes couleurs et les mêmes libellés. Ouvrir dessus,
 * c'est ouvrir sur une redondance — et faire du pipeline une vue qu'on n'ouvre
 * jamais, alors que tout le chantier existe parce que le flux d'un run était
 * illisible.
 *
 * ## Ce qui est écarté, et pourquoi
 *
 * **Deux routes** (`/runs/<id>/pipeline`, `/runs/<id>/kanban`), sur le modèle des
 * onglets d'une fiche agent (`lib/agents`). Le patron existe, il rendrait le
 * choix partageable par URL — et il coûterait un remontage complet de la vue à
 * chaque aller-retour : la tête, le journal et la lecture de l'autre vue
 * repartiraient, pour un changement qui ne change **rien** aux données. Les deux
 * lectures portent le *même* run, déjà chargé ; les séparer par une frontière de
 * route serait payer une navigation pour un changement de regard. Le prix assumé
 * est qu'on partage un **run**, pas la façon dont on le regarde.
 *
 * **Le Kanban retiré de la vue d'un run**, l'autre branche de l'alternative du
 * ticket. Elle se défend — mais elle laisserait sans rien les runs dont le plan
 * n'est pas connu (`plan_connu: false` : moteur antérieur, journal rejoué,
 * planification en échec), où le graphe se réduit aux tâches vues et sans aucune
 * arête. Retirer une vue livrée pour la remplacer par une autre qui se dégrade
 * sur ces runs-là échangerait un défaut contre un autre.
 */

import { IconeGraphe, IconeTache } from "@/components/Icones";
import type { Icone } from "@/components/Primitives";

/** Le flux du run : ses nœuds, ses arêtes, ses branches parallèles (#491). */
export const VUE_PIPELINE = "pipeline";
/** Ses tâches par statut, dans les colonnes du tableau de bord (#475). */
export const VUE_KANBAN = "kanban";

export type VueRunCle = typeof VUE_PIPELINE | typeof VUE_KANBAN;

export type VueRunOnglet = {
  cle: VueRunCle;
  /** Libellé de l'onglet — aussi ce que lit un lecteur d'écran. */
  libelle: string;
  /** Ce à quoi cette lecture répond, en une ligne : l'infobulle de l'onglet. */
  question: string;
  /** Le pictogramme de la facette, pris au jeu commun (#245). Décoratif. */
  icone: Icone;
};

/** L'ordre d'affichage : le flux d'abord, l'inventaire ensuite. */
export const VUES_RUN: VueRunOnglet[] = [
  {
    cle: VUE_PIPELINE,
    libelle: "Pipeline",
    question: "Quoi après quoi : le flux du run, ses branches et ses checklists",
    icone: IconeGraphe,
  },
  {
    cle: VUE_KANBAN,
    libelle: "Kanban",
    question: "Combien dans quel état : les tâches de ce run par colonne",
    icone: IconeTache,
  },
];

/** La lecture sur laquelle la vue d'un run s'ouvre — voir l'arbitrage ci-dessus. */
export const VUE_RUN_DEFAUT: VueRunCle = VUE_PIPELINE;

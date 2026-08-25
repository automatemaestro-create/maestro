/**
 * Les trois **lectures** d'un run et la bascule entre elles (#491, lot 3 de
 * #488 ; troisième position ajoutée par #516) — l'arbitrage que ces tickets
 * demandaient de rendre, écrit ici et une seule fois.
 *
 * ## Ce qui est tranché
 *
 * **Les trois vues coexistent, sous une bascule, et le pipeline est le défaut.**
 * Elles ne se remplacent pas, parce qu'elles ne répondent pas à la même question
 * (docs/05 §2.4.2) : le pipeline dit **« quoi après quoi »**, le Kanban
 * **« combien dans quel état »**, le journal **« qu'a-t-il fait »**. Aucune ne se
 * déduit d'une autre — on ne lit pas un enchaînement dans cinq colonnes, on ne
 * compte pas un état dans un graphe, et ni l'un ni l'autre ne rend ce qu'un run
 * a *dit*.
 *
 * **Mais elles ne s'affichent jamais ensemble**, et c'est le cœur de l'arbitrage :
 * empilées, elles se concurrenceraient — les mêmes tâches deux fois sur le même
 * écran, sans que rien ne dise laquelle regarder. C'est ce que #488 interdit en
 * toutes lettres (« jamais laissé aux deux écrans de se concurrencer »). Une
 * bascule n'en montre qu'une, et le geste de passer de l'une à l'autre *est* la
 * question qu'on se pose.
 *
 * **Le pipeline ouvre.** Le Kanban d'un run a été livré quatre lots plus tôt
 * (#475) et c'était le bon ordre, mais sa question est déjà à moitié répondue
 * au-dessus de lui : la barre de progression de l'en-tête compte par
 * compartiment, avec les mêmes couleurs et les mêmes libellés. Ouvrir dessus,
 * c'est ouvrir sur une redondance — et faire du pipeline une vue qu'on n'ouvre
 * jamais, alors que tout le chantier existe parce que le flux d'un run était
 * illisible.
 *
 * **Le journal est un onglet, plus un pied de page** (#516). #478 l'avait posé
 * **sous** les tâches, hors de toute bascule, avec une raison qui tenait alors :
 * le Kanban répond à « où en est-il ? », le journal à « qu'a-t-il fait ? », et on
 * ne consulte le second qu'après avoir lu le premier. #491 a ensuite glissé la
 * bascule au-dessus de lui — et c'est leur **superposition** qui fait le défaut,
 * pas l'une ou l'autre décision : rendu en dehors du `vue === …`, le journal
 * s'affichait sous **les deux** lectures, donc sous le pipeline, qui est le
 * défaut. Rien à l'écran ne disait qu'il n'appartenait pas à l'onglet ouvert, et
 * un fil d'événements collé sous un graphe se lit comme le détail de ce graphe.
 * L'ordre de lecture que #478 défendait est **conservé** — le journal ferme la
 * bascule, on l'atteint en dernier —, mais il est désormais porté par la
 * position de l'onglet et non par un empilement.
 *
 * ## Ce qui est écarté, et pourquoi
 *
 * **Trois routes** (`/runs/<id>/pipeline`, `…/kanban`, `…/journal`), sur le
 * modèle des onglets d'une fiche agent (`lib/agents`). Le patron existe, il
 * rendrait le choix partageable par URL — et il coûterait un remontage complet de
 * la vue à chaque aller-retour : la tête et les autres lectures repartiraient,
 * pour un changement qui ne change **rien** aux données. Les trois lectures
 * portent le *même* run, déjà chargé ; les séparer par une frontière de route
 * serait payer une navigation pour un changement de regard. Le prix assumé est
 * qu'on partage un **run**, pas la façon dont on le regarde.
 *
 * **Le Kanban retiré de la vue d'un run**, l'autre branche de l'alternative de
 * #491. Elle se défend — mais elle laisserait sans rien les runs dont le plan
 * n'est pas connu (`plan_connu: false` : moteur antérieur, journal rejoué,
 * planification en échec), où le graphe se réduit aux tâches vues et sans aucune
 * arête. Retirer une vue livrée pour la remplacer par une autre qui se dégrade
 * sur ces runs-là échangerait un défaut contre un autre.
 *
 * **Un cache pour garder le journal monté** hors de son onglet. Le monter dans la
 * bascule le **démonte** quand on regarde ailleurs, donc il se relit au retour —
 * c'est le comportement voulu, le fil étant repris à l'ouverture (il part du
 * persisté, jamais du fil du shell : `components/runs/JournalRun`, #478), et non
 * une régression à compenser.
 */

import { IconeGraphe, IconeJournal, IconeTache } from "@/components/Icones";
import type { Icone } from "@/components/Primitives";

/** Le flux du run : ses nœuds, ses arêtes, ses branches parallèles (#491). */
export const VUE_PIPELINE = "pipeline";
/** Ses tâches par statut, dans les colonnes du tableau de bord (#475). */
export const VUE_KANBAN = "kanban";
/** Ce qu'il a dit, dans l'ordre où il l'a dit — son journal persisté (#478). */
export const VUE_JOURNAL = "journal";

export type VueRunCle =
  | typeof VUE_PIPELINE
  | typeof VUE_KANBAN
  | typeof VUE_JOURNAL;

export type VueRunOnglet = {
  cle: VueRunCle;
  /** Libellé de l'onglet — aussi ce que lit un lecteur d'écran. */
  libelle: string;
  /** Ce à quoi cette lecture répond, en une ligne : l'infobulle de l'onglet. */
  question: string;
  /** Le pictogramme de la facette, pris au jeu commun (#245). Décoratif. */
  icone: Icone;
};

/**
 * L'ordre d'affichage : le flux d'abord, l'inventaire ensuite, le récit en
 * dernier. C'est l'ordre de lecture que #478 défendait quand le journal était au
 * pied de la vue — on le consulte après avoir vu où en est le run —, reporté sur
 * la bascule au lieu d'un empilement (#516).
 */
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
  {
    cle: VUE_JOURNAL,
    libelle: "Journal",
    question: "Qu'a-t-il fait : les événements de ce run, du plus récent au plus ancien",
    icone: IconeJournal,
  },
];

/** La lecture sur laquelle la vue d'un run s'ouvre — voir l'arbitrage ci-dessus. */
export const VUE_RUN_DEFAUT: VueRunCle = VUE_PIPELINE;

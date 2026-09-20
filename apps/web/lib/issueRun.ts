/**
 * **Ce qu'un run rend en finissant** (#928, lot 7 de #921) — le verdict, le
 * chemin du livrable, et de quoi le porter aux deux surfaces qui l'annoncent.
 *
 * Le constat qui ouvre le ticket est le plus net du retex du 2026-09-11 (G1),
 * et son auteur l'appelle « le dernier mètre » :
 *
 * > Un run qui se termine ne prévient personne, et ne dit pas où est le
 * > livrable. Ni le fil, ni la cloche, ni le tableau de bord ne signalent la
 * > fin ; aucun écran ne donne le chemin du résultat.
 *
 * Le run avait duré 53 minutes et coûté 12,51 $. Le livrable fonctionnait.
 * L'utilisateur ne l'a su qu'en allant regarder le disque.
 *
 * ## Trois décisions portent ce module
 *
 * **1. L'annonce est DÉRIVÉE du persisté, jamais un événement de plus.** C'est
 * le troisième critère du ticket — « un run terminé pendant qu'on regardait
 * ailleurs se retrouve » — et il interdit une annonce qui ne serait qu'un
 * passage temps réel. Or le flux d'événements du contexte global
 * (`useControlTower`) **part vide à chaque chargement** : il n'accumule que ce
 * que la WebSocket apporte depuis l'ouverture de l'écran, si bien qu'une fin
 * de run arrivée pendant qu'on était ailleurs n'y est plus. Les `executions`,
 * elles, sont **rechargées par le REST** à chaque montage et à chaque
 * reconnexion : un run soldé il y a trois heures y est, avec son statut, son
 * volume, son coût et son heure de fin. L'annonce se lit donc là, et le
 * critère 3 est tenu par construction — sans rien à écrire, à purger ou à
 * acquitter côté backend. Même raison que le journal d'un run, qui part du
 * persisté depuis #478.
 *
 * **2. Le livrable, c'est la RACINE DU PROJET du run.** C'est là que le travail
 * atterrit dans les deux régimes que le moteur connaît (`maestro/engine/
 * executor.py`) : fusionné dans la racine pour un projet versionné
 * (`fusion_faite`, #705), écrit **en place** dans la racine pour un projet qui
 * ne l'est pas (`ecriture_en_place`, #839). Le résumé d'un run ne porte pas de
 * chemin — il porte un `projet_id` —, et c'est le **projet actif** du contexte
 * global (`lib/etatGlobal`) qui le résout. Celui-là et pas la liste des projets
 * (`lib/etatProjetActif`), pour une raison de portée autant que de montage :
 * toute la Control Tower se lit **dans le cadre d'un projet** (#277, #281), donc
 * les runs servis sont les siens ; et `useEtatGlobal` est déjà ce dont le fil et
 * la cloche dépendent, là où `useProjetActif` aurait ajouté un fournisseur à
 * monter partout où l'on rend un fil. Aucun appel de plus, et le chemin est
 * celui que l'écran Projets affiche déjà.
 *
 * **3. « Aucun livrable » S'ÉCRIT.** Parti pris 2 de la veille de conception
 * (commentaire de #928), d'après la page d'un run terminé de GitHub Actions,
 * dont la colonne `Artifacts` affiche `–` **à la place qu'elle occuperait**.
 * Un run sans projet, ou dont le projet n'est plus déclaré, ne rend donc pas
 * `null` en silence : il rend sa raison, et l'annonce l'affiche. C'est la règle
 * de #839 appliquée à l'annonce — aucune issue n'est muette.
 *
 * ## Ce que ce module ne fait pas
 *
 * Il ne **formate** rien (c'est `lib/format`), ne rend aucun composant (c'est
 * `components/runs/AnnonceIssueRun`) et ne sait pas ouvrir un dossier (c'est
 * `lib/poste`, une capacité de la fenêtre). Il répond à une seule question :
 * *ce run a-t-il fini, et qu'a-t-il laissé ?*
 */

import { estSolde } from "./execution";
import {
  EXECUTION_ECHEC,
  EXECUTION_TERMINEE,
  type MessageChat,
  type Projet,
  type ResumeExecution,
} from "./types";

/**
 * Pourquoi un run soldé n'a **pas** de chemin à remettre.
 *
 * Deux codes, et pas un fourre-tout : ils n'appellent pas le même geste. Un run
 * **hors projet** n'a jamais eu de racine — c'est un travail de passage
 * (`maestro/engine/executor.py` : la tâche a travaillé dans un espace jetable),
 * et il n'y a rien à aller voir. Un run **hors du cadre** en a une, mais dans un
 * autre projet que celui qu'on regarde : le chemin existe, il n'est simplement
 * pas à nous de l'afficher ici — l'ouvrir depuis ce projet donnerait à croire
 * que le travail courant a atterri là.
 */
export const SANS_LIVRABLE_HORS_PROJET = "hors_projet";
export const SANS_LIVRABLE_HORS_CADRE = "hors_cadre";

/** Ce qu'un run soldé remet — le contenu de l'annonce, aux deux surfaces. */
export type IssueRun = {
  /** Le run, tel que le REST l'a rendu : objectif, volume, coût, bornes. */
  execution: ResumeExecution;
  /**
   * Le run a-t-il **abouti** ? `true` pour `terminee`, `false` pour tout autre
   * statut soldé — échec, annulation. Un booléen et non le statut : l'annonce
   * ne distingue que « c'est fait » de « ce n'est pas fait », le détail (la
   * cause, le statut exact) se lisant sur la vue du run.
   */
  abouti: boolean;
  /**
   * La racine du projet — **le chemin du livrable** —, ou `null` quand il n'y
   * en a pas. Dans ce cas `sansLivrable` en donne la raison : les deux vont
   * toujours ensemble, jamais un `null` muet.
   */
  racine: string | null;
  /** Pourquoi il n'y a pas de chemin (`SANS_LIVRABLE_*`), `null` s'il y en a un. */
  sansLivrable: string | null;
  /** Le nom du projet, pour nommer ce qu'on ouvre — vide sans projet connu. */
  projet: string;
};

/**
 * Ce qu'un run soldé remet — `null` tant qu'il n'a pas fini.
 *
 * Le seul point d'entrée : les deux surfaces qui annoncent (le fil, la cloche)
 * passent par lui, si bien qu'elles ne peuvent pas diverger sur ce qu'est une
 * fin de run ni sur où est le livrable.
 */
export function issueDuRun(
  execution: ResumeExecution,
  projet: Projet,
): IssueRun | null {
  if (!estSolde(execution)) return null;
  const sien = execution.projet_id === projet.id;
  const sansLivrable = sien
    ? null
    : execution.projet_id === null
      ? SANS_LIVRABLE_HORS_PROJET
      : SANS_LIVRABLE_HORS_CADRE;
  return {
    execution,
    abouti: execution.statut === EXECUTION_TERMINEE,
    racine: sien ? projet.racine : null,
    sansLivrable,
    projet: sien ? projet.nom : "",
  };
}

/**
 * Les runs soldés que **ce fil** a ouverts, dans l'ordre où il les a ouverts.
 *
 * Le rattachement vient de `message.run_id` (#268) : il est posé par le backend
 * sur la réponse qui a lancé le run, il est **persisté**, et il survit au
 * rechargement — c'est lui qui fait que l'annonce se pose dans la conversation
 * où le travail a été demandé, et dans aucune autre. Un run lancé ailleurs (la
 * page Runs, un autre poste) n'apparaît donc pas ici : il n'a pas été demandé
 * dans ce fil, et la cloche est là pour lui.
 *
 * Un même run n'est annoncé **qu'une fois**, même si plusieurs messages le
 * nomment : c'est une fin, pas une répétition.
 */
export function issuesDuFil(
  messages: MessageChat[],
  executions: ResumeExecution[],
  projet: Projet,
): IssueRun[] {
  const vus = new Set<string>();
  const issues: IssueRun[] = [];
  for (const message of messages) {
    const runId = message.run_id ?? "";
    if (runId === "" || vus.has(runId)) continue;
    vus.add(runId);
    const execution = executions.find((candidat) => candidat.run_id === runId);
    if (execution === undefined) continue;
    const issue = issueDuRun(execution, projet);
    if (issue !== null) issues.push(issue);
  }
  return issues;
}

/**
 * Les runs soldés **les plus récemment finis**, pour la cloche.
 *
 * Triés sur `fin` et non sur l'ordre de la liste : ce que la cloche doit dire
 * en premier est ce qui vient d'arriver. Un run soldé sans horodatage de fin —
 * un journal rejoué, un producteur minimaliste — n'est pas écarté pour autant,
 * il passe simplement derrière ceux qui en portent un : le perdre serait taire
 * une fin, ce que ce lot existe pour empêcher.
 */
export function issuesRecentes(
  executions: ResumeExecution[],
  projet: Projet,
  limite: number,
): IssueRun[] {
  return executions
    .map((execution) => issueDuRun(execution, projet))
    .filter((issue): issue is IssueRun => issue !== null)
    .sort((a, b) => (b.execution.fin ?? "").localeCompare(a.execution.fin ?? ""))
    .slice(0, limite);
}

/**
 * La clé du **repère de lecture** de la cloche, dans la mémoire du poste.
 *
 * Exportée pour les tests et pour la purge : rien d'autre ne doit l'écrire.
 */
export const CLE_ISSUES_VUES = "maestro.issues.vues";

/**
 * L'horodatage de la dernière fois que la cloche a été **ouverte** — ce qui
 * précède est lu, ce qui suit est neuf.
 *
 * Dans le `localStorage` comme les préférences d'affichage (`lib/preferences`)
 * et pour la même raison : c'est un fait du **poste**, pas de l'orchestration.
 * Deux postes qui regardent le même projet n'ont pas lu les mêmes choses, et
 * l'API n'a pas à le savoir.
 *
 * Chaîne vide quand rien n'a jamais été lu, ou que le stockage est refusé
 * (navigation privée, cookies bloqués) — auquel cas tout est neuf, ce qui est
 * le sens qui ne fait rien disparaître.
 */
export function lireIssuesVues(): string {
  try {
    return window.localStorage.getItem(CLE_ISSUES_VUES) ?? "";
  } catch {
    return "";
  }
}

/** Pose le repère à `horodatage` — l'ouverture de la cloche, et rien d'autre. */
export function ecrireIssuesVues(horodatage: string): void {
  try {
    window.localStorage.setItem(CLE_ISSUES_VUES, horodatage);
  } catch {
    // Stockage indisponible : la marque restera allumée, ce qui redit une
    // nouvelle déjà lue — l'inverse en tairait une qui ne l'est pas.
  }
}

/**
 * Y a-t-il une fin de run **non lue** ? C'est ce que la cloche porte en marque.
 *
 * Un **point**, pas un second compteur (voir `CentreNotifications`) : le chiffre
 * de la pastille répond « combien de choses m'attendent » (#322), et une fin de
 * run n'attend rien. Deux chiffres côte à côte obligeraient à en faire la
 * somme ; un point dit « il y a du neuf » sans se mêler du compte.
 *
 * Une issue **sans horodatage de fin** ne fait jamais rougir la marque : on ne
 * saurait pas la comparer au repère, et l'allumer sans pouvoir l'éteindre est
 * pire que se taire — c'est le seul endroit où l'on préfère le silence, et il
 * est borné à ce cas-là.
 */
export function aDesIssuesNonLues(issues: IssueRun[], vuesJusqua: string): boolean {
  return issues.some((issue) => {
    const fin = issue.execution.fin ?? "";
    return fin !== "" && fin > vuesJusqua;
  });
}

/**
 * Le libellé de l'issue — « Run terminé », « Run en échec », « Run interrompu ».
 *
 * Ici et non dans `lib/format` (`libelleStatutExecution`), parce que ce n'est
 * pas le nom d'un statut mais le **verdict d'une fin** : la liste des runs dit
 * « Terminée » d'une exécution, l'annonce dit ce qui vient d'arriver à celui
 * dont on avait demandé le travail.
 */
export function libelleIssue(issue: IssueRun): string {
  if (issue.abouti) return "Run terminé";
  return issue.execution.statut === EXECUTION_ECHEC
    ? "Run en échec"
    : "Run interrompu";
}

/**
 * Ce qu'on dit à la place du chemin quand il n'y en a pas — jamais rien.
 *
 * `null` pour une issue qui **a** un chemin : l'appelant affiche alors la
 * racine. Les deux branches sont exclusives et couvrent tout, ce qui est la
 * forme que le parti pris 2 demande.
 */
export function raisonSansLivrable(issue: IssueRun): string | null {
  switch (issue.sansLivrable) {
    case SANS_LIVRABLE_HORS_PROJET:
      return "aucun projet — ce run n'a écrit dans aucune racine";
    case SANS_LIVRABLE_HORS_CADRE:
      return "un autre projet — son chemin se lit depuis celui-là";
    default:
      return null;
  }
}

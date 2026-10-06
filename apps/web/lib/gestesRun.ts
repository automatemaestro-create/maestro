/**
 * **Les gestes sur un run depuis le fil** (#1179) : ce qui attend une confirmation,
 * et les mots de chaque geste.
 *
 * Le fil de l'orchestrateur agit sur les runs — les mettre en pause, les reprendre,
 * les interrompre, les relancer — par le **même** service que les boutons des écrans
 * des runs (`runs/EtatRun`, `PanneauRunsImmobiles`). Chaque geste qui a un effet est
 * proposé sur une carte (`chat/GesteSurUnRun`), confirmé d'un clic ou d'un « oui »,
 * puis exécuté ; ce qu'il a donné se lit sous la réponse (`TraceDuGeste`).
 *
 * ## Un seul vocabulaire, celui des écrans
 *
 * Le fil et l'écran d'un run nomment les gestes des mêmes mots (#571) : « Mettre en
 * pause », « Reprendre », « Interrompre » — l'annulation s'appelle **interrompre** à
 * l'écran depuis #467, et le fil ne lui donne pas un second nom —, « Relancer ». Les
 * phrases qui disent ce qu'un geste **fait** vivent ici une fois, et les écrans des
 * runs les lisent aussi (`PHRASE_PAUSE`, `PHRASE_INTERRUPTION`) : deux formulations de
 * « ce que fait une pause » finiraient par ne plus dire la même chose.
 *
 * ## Ce que la variante retenue a tranché (#1179)
 *
 * La forme vient de la veille de conception du ticket et de la variante retenue sur
 * pièces par le regard neuf (commentaires « Veille de conception » et « Variante
 * retenue ») : la carte **pose la question** au verbe du geste, **nomme le run** à ses
 * faits, **dit ce qui va se passer**, puis porte un seul geste principal — d'après
 * l'approbation d'outil de VS Code et le retour arrière de Replit ; une fois confirmé,
 * une **ligne cochée** sous la réponse dit l'état relu — d'après la trace de VS Code.
 */

import type {
  GesteRun,
  GesteRunPropose,
  MessageChat,
  ResumeExecution,
  RunVise,
} from "@/lib/types";

/**
 * Ce qu'une pause **fait**, en toutes lettres (#477) — la phrase de l'écran d'un run
 * (`LignePause`) et de la carte du fil. Une tâche tuée en cours perd son travail :
 * d'où « on ne lance plus » plutôt que « on interrompt ».
 */
export const PHRASE_PAUSE =
  "Aucune tâche nouvelle n'est lancée ; celles qui étaient en vol vont à leur terme. Le run reprendra son plan là où il en est.";

/**
 * Ce qu'une interruption **coûte** (#467) — la phrase du bouton « Interrompre » armé,
 * et celle de la carte du fil : c'est le seul geste sans retour.
 */
export const PHRASE_INTERRUPTION =
  "Les tâches en vol sont tuées là où elles en sont et perdent leur travail. C'est sans retour.";

/**
 * Ce qu'une interruption coûte à un run **en pause** (#477) : rien n'est parti depuis la
 * pause, et seule une tâche partie avant elle peut tourner encore. La phrase du bouton,
 * lue sur la carte d'un run suspendu, lui prêtait des tâches en vol que le modèle,
 * juste au-dessus, disait n'avoir jamais démarré — deux réponses à « que va-t-il se
 * passer ? » (relecture visuelle de #1179).
 */
export const PHRASE_INTERRUPTION_EN_PAUSE =
  "Les tâches qui attendaient ne partiront plus, et une tâche partie avant la pause, si elle tourne encore, perd son travail. C'est sans retour.";

/** Les mots d'un geste : sa question, son verbe, ce qu'il fait, et comment il se dit fait. */
export type LibellesDuGeste = {
  /** Le titre de la carte — la question que la confirmation tranche. */
  question: string;
  /** Le bouton principal, au verbe des écrans des runs. */
  verbe: string;
  /** Ce que le geste fera, dit **avant** qu'il parte. */
  consequence: string;
  /** Ce que la trace dit une fois le geste confirmé et relu. */
  fait: string;
};

export const LIBELLES_DES_GESTES: Record<GesteRun, LibellesDuGeste> = {
  pause: {
    question: "Mettre ce run en pause ?",
    verbe: "Mettre en pause",
    consequence: PHRASE_PAUSE,
    fait: "Mis en pause",
  },
  reprise: {
    question: "Reprendre ce run ?",
    verbe: "Reprendre",
    // Vrai d'un run en pause comme d'un run interrompu (#1391) : ce qui est fait ne
    // repart pas, et seule une tâche coupée en vol par l'extinction recommence.
    consequence:
      "Le run repart là où il en était, le même : ce qui est fait reste fait, seul ce qui reste s'exécute.",
    fait: "Repris",
  },
  annulation: {
    question: "Interrompre ce run ?",
    verbe: "Interrompre",
    consequence: PHRASE_INTERRUPTION,
    fait: "Interrompu",
  },
  relance: {
    question: "Relancer ce run ?",
    verbe: "Relancer",
    consequence:
      "Un nouveau run repart de son brief approuvé, sans repayer le cadrage, et redécoupe le travail en tâches ; celui-ci est soldé.",
    fait: "Relancé",
  },
};

/**
 * Les mots d'un geste — ou `null` pour une action que l'écran ne connaît pas : le
 * backend a pu s'enrichir, et une carte sans question ni verbe ne s'affiche pas.
 */
export function libellesDuGeste(action: string): LibellesDuGeste | null {
  return (LIBELLES_DES_GESTES as Record<string, LibellesDuGeste>)[action] ?? null;
}

/**
 * Ce que **ce** geste fera à **ce** run — la conséquence de ses libellés, sauf pour
 * l'interruption d'un run en pause, qui n'a plus de tâches en vol à tuer au sens du
 * bouton (`PHRASE_INTERRUPTION_EN_PAUSE`). `null` pour une action inconnue.
 */
export function consequenceDuGeste(demande: GesteRunPropose): string | null {
  const libelles = libellesDuGeste(demande.action);
  if (libelles === null) return null;
  if (demande.action === "annulation" && demande.run.en_pause) {
    return PHRASE_INTERRUPTION_EN_PAUSE;
  }
  return libelles.consequence;
}

/**
 * Le **geste sur un run** que ce fil propose encore — `null` s'il n'y en a pas (#1179).
 *
 * Le dernier message, et lui seul : la règle de `geste_run_en_attente`
 * (`maestro/controltower/chat.py`), la même que celle des cinq autres demandes du
 * fil. Une carte attend tant que rien ne l'a suivie ; ce qui la solde est qu'on y
 * ait répondu, quoi qu'on ait répondu.
 */
export function gesteRunEnAttente(messages: MessageChat[]): GesteRunPropose | null {
  const dernier = messages.at(-1);
  return dernier?.geste_run ?? null;
}

/**
 * Le run d'une carte ou d'une trace, vu comme un **résumé** — de quoi le dire avec le
 * badge des écrans des runs (`BadgeRun`), au lieu d'en écrire un second.
 *
 * Ce que le fil a recopié du run au moment d'en parler (statut, pause, titre) ; le
 * reste — coût, tâches, vitalité — n'y est pas, et le badge ne le lit pas.
 */
export function resumeDuRunVise(run: RunVise): ResumeExecution {
  return {
    run_id: run.run_id,
    objectif: run.titre,
    titre: run.titre,
    statut: run.statut,
    en_pause: run.en_pause,
    nb_taches: 0,
    cout_usd: null,
    ticket: null,
    projet_id: null,
    debut: "",
    fin: null,
  };
}

/**
 * Ce que les écrans savent d'une **question d'agent** hors du JSX (#1025) :
 * lesquelles attendent encore, lesquelles un fil donné concerne, et si l'agent
 * est déjà reparti sans réponse.
 *
 * Ces règles vivent ici et pas dans les composants pour la raison habituelle du
 * dépôt, celle qui a fait `lib/brief` : elles sont **partagées** — le pied du
 * fil de `/chat`, l'onglet Chat d'une fiche agent et le badge de la cloche
 * posent tous la question « qu'est-ce qui attend une réponse ? », et trois
 * formulations finiraient par diverger. C'est exactement le défaut que le retex
 * du 2026-09-11 avait trouvé sur le cadrage (G10) : un panneau qui disait
 * « aucun » pendant que la question était posée, parce qu'il regardait autre
 * chose.
 *
 * ## Ce qu'une question n'est pas
 *
 * Une **validation** (#48). Là-bas on approuve ou on refuse un acte, ici on
 * répond du texte à une question, et répondre n'autorise aucun appel d'outil
 * (EF-08, docs/32 §5). Les deux files ne se mélangent donc jamais — sauf dans
 * **un** compte, celui de la cloche, qui répond à « combien de choses
 * m'attendent ? » et non à « combien de validations » (#322).
 */

import {
  QUESTION_EN_ATTENTE,
  type Question,
} from "./types";

/**
 * Le **libellé de menu** de la page où l'on répond à une question d'agent.
 *
 * Un libellé et non un chemin, comme partout depuis #191 : le renvoi suit sa
 * page si elle déménage, et ne s'allume pas vers une page absente.
 *
 * Il vaut « Chat », comme `PAGE_DU_CADRAGE` (`lib/brief`), et ce n'est pas un
 * doublon à fondre : les deux répondent à deux questions — *où se tranche un
 * cadrage ?* et *où se répond une question d'agent ?* — qui ont aujourd'hui la
 * même réponse et pourraient cesser de l'avoir. Les confondre ferait déménager
 * l'une le jour où l'autre bouge.
 */
export const PAGE_DES_QUESTIONS = "Chat";

/**
 * Les questions qui attendent encore une réponse, **la plus ancienne d'abord**.
 *
 * L'ordre est la moitié du signal, comme pour `runsEnAttente` (`lib/brief`) :
 * deux questions ne se valent pas, et celle qui dort depuis dix minutes est
 * celle dont l'agent est déjà reparti sans elle.
 *
 * Le tri porte sur `horodatage` — l'instant où la question a été posée — et non
 * sur `echeance` : deux questions posées ensemble peuvent avoir des bornes
 * différentes (le réglage peut changer entre deux runs), et c'est bien
 * l'ancienneté de la **demande** qui dit laquelle on a laissée de côté. Un
 * horodatage manquant passe en dernier plutôt que de remonter en tête par une
 * chaîne vide — même repli que `attenteDepuis`.
 */
export function questionsEnAttente(questions: Question[]): Question[] {
  return questions
    .filter((question) => question.statut === QUESTION_EN_ATTENTE)
    .slice()
    .sort((a, b) => {
      const [ga, gb] = [a.horodatage || "9999", b.horodatage || "9999"];
      return ga < gb ? -1 : ga > gb ? 1 : 0;
    });
}

/**
 * Les questions en attente que **ce fil** concerne.
 *
 * Deux cas, et un seul principe — *on répond là où l'on est* :
 *
 * - le fil de l'**orchestration** porte **toutes** les questions du projet. Il
 *   est la porte d'entrée (docs/29) et l'endroit où « brief, clarifications et
 *   validation se décident » (#483) ; c'est aussi là que la cloche achemine. Une
 *   question de `bdd` qui n'apparaîtrait que dans l'aparté avec `bdd`
 *   n'atteindrait personne : il faudrait savoir qu'elle existe pour aller la
 *   chercher, et c'est précisément ce que le badge existe pour éviter ;
 * - un **aparté avec `@agent`** ne porte que les siennes. On y parle à cet
 *   agent-là ; lui montrer la question d'un autre ferait répondre à côté.
 *
 * `destinataire` est le fil affiché, `orchestration` celui qui vaut « tous ».
 * L'appelant passe les deux plutôt qu'un booléen : le nom du fil de
 * l'orchestration est une constante de `lib/orchestration`, et ce module n'a pas
 * à la connaître pour appliquer la règle.
 */
export function questionsDuFil(
  questions: Question[],
  destinataire: string,
  orchestration: string,
): Question[] {
  const attente = questionsEnAttente(questions);
  if (destinataire === orchestration) return attente;
  return attente.filter((question) => question.agent === destinataire);
}

/**
 * L'agent est-il **déjà reparti** sans réponse ? (troisième critère de #1025)
 *
 * La question reste servie et reste répondable — une réponse tardive sert encore
 * (`MemoireArbitrage`, #584), et c'est pour ça que l'API ne la ferme pas. Ce qui
 * change est ce que l'écran en dit : avant la borne il annonce ce qui *arrivera*
 * sans réponse, après il dit ce qui est *arrivé*.
 *
 * Le verdict se rend sur `echeance` — une **date**, servie par le canal — et
 * jamais sur la phrase `attente`, qui dit le même chiffre en toutes lettres :
 * reconnaître un nombre dans une phrase serait juger du texte par un motif, ce
 * que le dépôt refuse (#746). Il ne se rend pas non plus sur une borne recopiée
 * côté navigateur, qui ferait deux supports pour un réglage du moteur.
 *
 * `maintenant` vient de l'horloge partagée (`lib/horloge`) et vaut `null` tant
 * qu'elle n'a pas démarré : on rend alors `false`, c'est-à-dire « pas encore
 * échue ». Le repli est délibérément celui-là — annoncer que l'agent est reparti
 * alors qu'on ne sait pas l'heure serait affirmer un fait faux, tandis que le
 * taire une seconde ne coûte que la seconde. Une `echeance` vide (question
 * d'avant ce lot) rend `false` pour la même raison.
 */
export function questionEchue(
  question: Question,
  maintenant: number | null,
): boolean {
  if (question.echeance === "" || maintenant === null) return false;
  const borne = Date.parse(question.echeance);
  if (Number.isNaN(borne)) return false;
  return maintenant >= borne;
}

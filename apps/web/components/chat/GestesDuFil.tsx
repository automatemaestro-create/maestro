"use client";

/**
 * **Les gestes du fil**, là où la main allait taper — et la règle qui dit
 * lesquels attendent (#1106).
 *
 * Trois demandes peuvent se poser au pied d'une conversation, et chacune a sa
 * carte : la **question d'un agent** (#1025), la **question d'outillage** d'un
 * projet neuf (#1031) et la **demande de cadrage** — « Je lance ? » (#943,
 * bornée par #990). Ce module ne décide d'aucune de leurs formes : elles ont été
 * tranchées chacune sur pièces, veille et variantes à l'appui, et ce fichier ne
 * fait que les **composer** dans l'ordre que #1025 a posé et que #1031 a étendu
 * d'un cran.
 *
 * ## Pourquoi il existe — le défaut que #1106 corrige
 *
 * Ce pied vivait **dans `app/chat/page.tsx`**. `ColonneConversation` (#926)
 * monte la même `Conversation`, sur le même fil, avec la même API… et sans lui :
 * depuis n'importe quel écran, la question d'un agent et la proposition de run
 * s'**affichaient** dans la colonne sans rien pour y répondre, et il fallait
 * ouvrir `/chat` — c'est-à-dire changer de page — pour lancer un run. C'est la
 * réserve C2 du bouclage de « L'atelier » (verdict du 2026-09-21).
 *
 * Le remède n'est pas de recopier le pied dans la colonne : ce serait deux
 * formulations de « qu'est-ce qui attend un geste ? », qui finiraient par ne
 * plus désigner la même chose — le défaut G10 du retex du 2026-09-11, qu'on ne
 * refait pas, et la raison d'être de `lib/brief`, `lib/outillage` et
 * `lib/questions`. La composition vit donc **ici**, une fois, et les deux
 * surfaces l'appellent.
 *
 * ## Un hook, et non un composant
 *
 * Le contrat de `Conversation` est que `pied` **absent** ne rend rien du tout :
 * `{pied !== undefined && <div className="mt-3">{pied}</div>}`. Un composant qui
 * rendrait `null` quand rien n'attend laisserait donc une boîte vide et son
 * `mt-3` sous le dernier message, c'est-à-dire 12 px de vide permanents dans un
 * fil au repos — sur toutes les surfaces, dont une colonne de 320 px. Le hook
 * rend `undefined` dans ce cas, et le contrat tient tel quel.
 *
 * ## Ce qui ne bouge pas
 *
 * - **le fil reste un seul composant pour deux emplacements** (docs/35 §3.3).
 *   Ce module ne prend aucune mesure, ne connaît aucun point de rupture et ne
 *   rend rien d'autre dans la colonne que sur `/chat` : les cartes sont montées
 *   **telles quelles**, comme le parti pris 1 de la veille de #926 l'exige pour
 *   le fil lui-même. Une carte qui tiendrait mal à 320 px est une mesure
 *   (`/banc-mise-en-page`), pas une seconde mise en page ;
 * - **l'ordre**, qui est celui de #1025 étendu par #1031 : ce qui est le plus
 *   loin de la saisie est ce qui a le moins à voir avec elle. Les questions
 *   d'agents d'abord — elles portent sur un travail déjà en vol ; la question
 *   d'outillage ensuite — elle se répond d'un choix, pas d'une frappe ; la
 *   demande de cadrage en dernier, parce que c'est la seule qui remplace
 *   vraiment la zone de saisie, son objectif étant éditable ;
 * - **les trois ne s'excluent pas.** Un fil peut porter une question d'agent
 *   **et** une proposition. Seules les deux qui vivent sur le **message** —
 *   proposition et question d'outillage — ne cohabitent jamais, un message ne
 *   portant que l'une ou l'autre.
 */

import { useMemo, type ReactNode } from "react";

import { DemandeDeCadrage } from "@/components/chat/DemandeDeCadrage";
import { QuestionDOutillage } from "@/components/chat/QuestionDOutillage";
import { QuestionsDuFil } from "@/components/chat/QuestionDansLeFil";
import { propositionEnAttente } from "@/lib/brief";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { questionEnAttente } from "@/lib/outillage";
import { questionsDuFil } from "@/lib/questions";
import type { Chat } from "@/lib/useChat";

/**
 * Ce qui attend un geste sur ce fil, prêt à passer en `pied` de `Conversation` —
 * ou `undefined` si rien n'attend.
 *
 * `destinataire` est le fil **affiché** : le fil de l'orchestration porte toutes
 * les questions du projet et les deux demandes qui vivent sur un message ; un
 * aparté `@agent` ne porte que les siennes (`lib/questions`, `lib/brief`,
 * `lib/outillage` — les règles sont appelées, jamais recopiées). L'appelant le
 * passe plutôt qu'un booléen, pour la raison de `questionsDuFil` : le nom du fil
 * global est une constante de `lib/orchestration`, et chaque surface sait lequel
 * elle montre.
 */
export function useGestesDuFil(
  fil: Chat,
  destinataire: string,
): ReactNode | undefined {
  const { questions: toutesLesQuestions, repondreAUneQuestion } =
    useEtatGlobal();
  const global = destinataire === AGENT_ORCHESTRATION;

  const questions = useMemo(
    () => questionsDuFil(toutesLesQuestions, destinataire, AGENT_ORCHESTRATION),
    [toutesLesQuestions, destinataire],
  );

  // Les deux demandes portées par le **dernier message**, et seulement sur le
  // fil de l'orchestration : elle est la seule à proposer des runs et à mener un
  // questionnaire d'outillage, donc un aparté avec un agent n'en porte jamais.
  const proposition = global ? propositionEnAttente(fil.messages) : null;
  const outillage = global ? questionEnAttente(fil.messages) : null;

  if (
    questions.length === 0 &&
    proposition === null &&
    !outillage?.question
  ) {
    return undefined;
  }

  return (
    <div className="flex flex-col gap-3">
      <QuestionsDuFil questions={questions} repondre={repondreAUneQuestion} />
      {outillage?.question && (
        /* La `key` remet la carte à zéro d'une question à la suivante — même
           geste et même raison que `FilDeCadrage` d'un tour de clarification au
           suivant. Sans elle, React réutilise l'instance (même position dans
           l'arbre), donc la sélection garde la recommandation de la question
           **précédente** : elle n'est plus une option de celle-ci, plus rien
           n'est coché, et le bouton enverrait une valeur que l'API refuserait.
           Mesuré sur la stack de démo avant de le corriger (#1031). */
        <QuestionDOutillage
          key={outillage.question.cle}
          question={outillage.question}
          repondre={fil.repondreQuestion}
          enCours={fil.envoi}
        />
      )}
      {proposition !== null && (
        <DemandeDeCadrage
          demande={proposition}
          trancher={fil.trancherCadrage}
          enCours={fil.envoi}
        />
      )}
    </div>
  );
}

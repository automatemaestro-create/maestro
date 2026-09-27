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
 * ⚠ L'outillage a **deux moments**, et le second a changé de forme avec #1161 :
 * tant qu'une question attend, c'est `QuestionDOutillage` ; ensuite, ce sont les
 * **pièces**, une à la fois — `PieceDOutillage`, le fichier montré avec ce qui
 * changera, puis écrit sur accord. Elle remplace la carte qui concluait le
 * questionnaire en écrivant tout d'un coup (`ConclusionOutillage`, #1104), partie
 * avec lui : l'outillage ne s'écrit plus en une fois. Question et pièce vivent sur le
 * message et ne cohabitent **jamais**, donc elles occupent le même rang ci-dessous.
 *
 * ⚠ Une quatrième demande est venue avec #1294 : la **proposition de projet**
 * (`DemandeDeProjet`), qui fait naître un projet dans la conversation. Elle vit
 * sur le message comme les autres et ne cohabite avec aucune — on ne propose pas
 * un run dans un projet qui n'existe pas encore —, donc elle prend elle aussi le
 * dernier rang, au plus près de la saisie, où une correction se tape. Elle est
 * surtout posée **sur la porte d'entrée**, avant tout projet : ce hook se passe
 * donc du shell quand il n'y en a pas (`useEtatGlobalFacultatif`) — sans projet,
 * il n'y a ni question d'agent ni outillage à conclure, et rien d'autre ne manque.
 *
 * ⚠ La demande de cadrage a, elle aussi, une remplaçante depuis #1146 : sur un
 * projet **sans agent**, l'orchestration ne propose pas de run, elle propose
 * l'équipe (`EquipeDansLeFil`). Les deux vivent sur le message et ne cohabitent
 * jamais — on ne propose pas un run qu'on sait voué à l'échec —, donc la carte
 * d'équipe prend le rang de la demande de cadrage : la dernière, au plus près de
 * la saisie. Une fois l'équipe créée, c'est la demande de cadrage qui revient à ce
 * rang, sur le travail d'origine.
 *
 * ⚠ Une sixième est venue avec #1179 : le **geste sur un run** (`GesteSurUnRun`) —
 * le mettre en pause, le reprendre, l'interrompre, le relancer —, à confirmer avant
 * qu'il parte. Elle vit sur le message comme les autres et ne cohabite avec aucune,
 * donc elle prend aussi le dernier rang ; la règle qui dit si elle attend encore est
 * celle de toutes (`gesteRunEnAttente`, `lib/gestesRun`).
 *
 * ⚠ #1183 y ajoute deux choses, et c'est ce qui fait régler **ici** ce qui attend
 * quelqu'un pendant un run :
 *
 * - les **validations en attente**, à côté des questions d'agents — dans la carte des
 *   validations elle-même (`CarteValidation`), que #1228 a écrite pour être montée
 *   « dans le fil sans rien recopier ». Elles suivent la règle des questions
 *   (`validationsDuFil`) et leur rang : ce sont deux attentes d'un travail déjà en vol ;
 * - la septième demande, le **règlement d'une attente** (`ReglementDansLeFil`) — la
 *   réponse à un agent ou la décision sur une validation que le fil a comprise d'une
 *   phrase, à confirmer. Elle vit sur le message et prend le dernier rang, comme le
 *   geste sur un run. La question ou la validation qu'elle vise **sort de sa pile** le
 *   temps que la carte attende : deux cartes pour une seule décision feraient trancher
 *   deux fois la même chose, et la carte du règlement la nomme déjà. « Pas maintenant »
 *   la rend à sa place.
 *
 * ⚠ Une huitième est venue avec #1182 : la **décision au plafond de dépense**
 * (`PlafondDansLeFil`) — un run qui a atteint son budget et attend qu'on relève,
 * réduise ou arrête. Elle ne vit pas sur un message mais sur le **run** (la liste
 * des exécutions du shell), et elle se pose **juste sous les questions d'agents et
 * les validations** : c'est le run entier qui attend, et le fil collant à son bas,
 * la carte la plus basse est la première vue — mais au-dessus de ce que porte le
 * dernier message.
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
 *   d'outillage ensuite — elle se répond d'un choix, ou avec ses mots, et depuis
 *   #1147 la zone de saisie juste dessous répond aussi (la carte le dit) ; la
 *   demande de cadrage en dernier, parce que c'est la seule qui remplace
 *   vraiment la zone de saisie, son objectif étant éditable ;
 * - **les trois ne s'excluent pas.** Un fil peut porter une question d'agent
 *   **et** une proposition. Seules les deux qui vivent sur le **message** —
 *   proposition et question d'outillage — ne cohabitent jamais, un message ne
 *   portant que l'une ou l'autre.
 */

import { useMemo, type ReactNode } from "react";

import { CarteValidation } from "@/components/CarteValidation";
import { DemandeDeCadrage } from "@/components/chat/DemandeDeCadrage";
import { DemandeDeProjet } from "@/components/chat/DemandeDeProjet";
import { EquipeDansLeFil } from "@/components/chat/EquipeDansLeFil";
import { GesteSurUnRun } from "@/components/chat/GesteSurUnRun";
import { PieceDOutillage } from "@/components/chat/PieceDOutillage";
import { PlafondDansLeFil } from "@/components/chat/PlafondDansLeFil";
import { QuestionDOutillage } from "@/components/chat/QuestionDOutillage";
import { QuestionsDuFil } from "@/components/chat/QuestionDansLeFil";
import { ReglementDansLeFil } from "@/components/chat/ReglementDansLeFil";
import { propositionEnAttente } from "@/lib/brief";
import { recrutementEnAttente } from "@/lib/equipe";
import { useEtatGlobalFacultatif } from "@/lib/etatGlobal";
import { gesteRunEnAttente } from "@/lib/gestesRun";
import { useHorloge } from "@/lib/horloge";
import { projetEnAttente } from "@/lib/naissance";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { pieceEnAttente, questionEnAttente } from "@/lib/outillage";
import { runsAuPlafond } from "@/lib/plafond";
import { questionsDuFil } from "@/lib/questions";
import { reglementEnAttente } from "@/lib/reglements";
import type { Question, Validation } from "@/lib/types";
import type { Chat } from "@/lib/useChat";
import { validationsDuFil } from "@/lib/validations";

/** Aucune question d'agent : ce que vaut le fil hors du shell (#1294). */
const AUCUNE_QUESTION: Question[] = [];

/** Aucune validation non plus, pour la même raison (#1183). */
const AUCUNE_VALIDATION: Validation[] = [];

/** Aucun run : hors du shell, aucun ne peut attendre sur son plafond (#1182). */
const AUCUN_RUN: never[] = [];

/** Rien à répondre hors du shell — aucune question n'y est jamais montrée. */
async function sansQuestion(): Promise<void> {}

/** Rien à trancher hors du shell — ni validation ni plafond n'y est jamais montré. */
async function sansDecision(): Promise<void> {}

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
  // Sans shell — la porte d'entrée, où un projet naît (#1294) — il n'y a aucune
  // question d'agent à montrer : elles sont celles des runs d'un projet.
  const etat = useEtatGlobalFacultatif();
  const toutesLesQuestions = etat?.questions ?? AUCUNE_QUESTION;
  const repondreAUneQuestion = etat?.repondreAUneQuestion ?? sansQuestion;
  const toutesLesValidations = etat?.validations ?? AUCUNE_VALIDATION;
  const decider = etat?.decider ?? sansDecision;
  const maintenant = useHorloge();
  const global = destinataire === AGENT_ORCHESTRATION;
  const executions = etat?.executions ?? AUCUN_RUN;
  const trancherPlafond = etat?.trancherPlafond ?? sansDecision;

  // Le règlement qu'une phrase a proposé (#1183) : son attente sort de sa pile tant
  // que la carte attend — une seule carte par décision (voir l'en-tête).
  const reglement = global ? reglementEnAttente(fil.messages) : null;
  const visee = reglement?.attente ?? null;

  const questions = useMemo(
    () =>
      questionsDuFil(toutesLesQuestions, destinataire, AGENT_ORCHESTRATION).filter(
        (question) =>
          !(visee?.genre === "question" && visee.identifiant === question.question_id),
      ),
    [toutesLesQuestions, destinataire, visee],
  );
  const validations = useMemo(
    () =>
      validationsDuFil(toutesLesValidations, destinataire, AGENT_ORCHESTRATION).filter(
        (validation) =>
          !(visee?.genre === "validation" && visee.identifiant === validation.tache_id),
      ),
    [toutesLesValidations, destinataire, visee],
  );

  // Les deux demandes portées par le **dernier message**, et seulement sur le
  // fil de l'orchestration : elle est la seule à proposer des runs et à mener un
  // questionnaire d'outillage, donc un aparté avec un agent n'en porte jamais.
  const proposition = global ? propositionEnAttente(fil.messages) : null;
  const outillage = global ? questionEnAttente(fil.messages) : null;
  const messageRecrutement = global ? recrutementEnAttente(fil.messages) : null;
  const recrutement = messageRecrutement?.recrutement ?? null;
  const projetPropose = global ? projetEnAttente(fil.messages) : null;
  const piece = global ? (pieceEnAttente(fil.messages)?.piece ?? null) : null;
  const geste = global ? gesteRunEnAttente(fil.messages) : null;
  // Les runs arrêtés sur leur plafond de dépense (#1182) — sur le seul fil de
  // l'orchestration, parce que c'est une décision **du run** : aucun agent ne
  // l'a posée, et un aparté avec l'un d'eux n'a pas à la porter.
  const auPlafond = useMemo(
    () => (global ? runsAuPlafond(executions) : []),
    [global, executions],
  );

  if (
    auPlafond.length === 0 &&
    questions.length === 0 &&
    validations.length === 0 &&
    proposition === null &&
    recrutement === null &&
    projetPropose === null &&
    !outillage?.question &&
    piece === null &&
    geste === null &&
    reglement === null
  ) {
    return undefined;
  }

  return (
    <div className="flex flex-col gap-3">
      <QuestionsDuFil questions={questions} repondre={repondreAUneQuestion} />
      {/* Les validations en attente (#1183), dans leur carte, montée telle quelle —
          keyée sur la tâche comme dans toutes ses files (#272, `CarteValidation`). */}
      {validations.map((validation) => (
        <CarteValidation
          key={validation.tache_id}
          validation={validation}
          decider={decider}
          maintenant={maintenant}
        />
      ))}
      {/* Le plafond de dépense **sous** les questions d'agents et les validations
          (#1182) : c'est le run entier qui attend, là où elles n'en retiennent
          qu'une tâche, et le fil colle à son bas — la carte la plus basse est celle
          qu'on voit. Posée au-dessus, elle restait hors de l'écran derrière une
          question déjà repartie sans réponse (mesuré sur la vraie stack). Au-dessus,
          en revanche, de ce que porte le dernier message, qui répond à ce qu'on
          vient de taper. La `key` est le run **et** le début de l'attente : une
          seconde question sur le même run (plafond relevé trop court) repart d'une
          carte neuve, pas des cases et du montant de la précédente. */}
      {auPlafond.map((run) => (
        <PlafondDansLeFil
          key={`${run.run_id}|${run.attente_depuis ?? ""}`}
          run={run}
          trancher={trancherPlafond}
        />
      ))}
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
          compris={outillage.comprehension ?? []}
          repondre={fil.repondreQuestion}
          depuisLeFil
          enCours={fil.envoi}
        />
      )}
      {piece !== null && (
        /* La `key` est l'**empreinte** de la version montrée : une pièce corrigée
           revient au même chemin, et la carte doit repartir repliée sur son
           nouveau diff plutôt que garder le dépli de la version d'avant. */
        <PieceDOutillage
          key={piece.empreinte}
          piece={piece}
          trancher={fil.trancherPiece}
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
      {recrutement !== null && (
        /* La `key` est le **projet et le run** : une demande reposée après un
           refus de création (même projet, même run) garde la proposition déjà
           composée et ce qu'on y avait ajusté, au lieu de redemander une analyse
           et N playbooks. Le run y entre depuis #1227 — un projet peut se voir
           proposer son équipe entière avant tout run, puis un renfort pendant un
           run, et les deux n'ont ni la même proposition ni les mêmes rôles
           cochés. */
        <EquipeDansLeFil
          key={`${recrutement.projet_id}|${recrutement.run_id ?? ""}`}
          demande={recrutement}
          cle={`${recrutement.projet_id}|${messageRecrutement?.horodatage ?? ""}`}
          recruter={fil.recruter}
          enCours={fil.envoi}
        />
      )}
      {projetPropose !== null && (
        <DemandeDeProjet
          demande={projetPropose}
          declarer={fil.declarerProjet}
          enCours={fil.envoi}
        />
      )}
      {geste !== null && (
        <GesteSurUnRun demande={geste} trancher={fil.trancherGeste} enCours={fil.envoi} />
      )}
      {reglement !== null && (
        <ReglementDansLeFil
          demande={reglement}
          trancher={fil.trancherReglement}
          enCours={fil.envoi}
        />
      )}
    </div>
  );
}

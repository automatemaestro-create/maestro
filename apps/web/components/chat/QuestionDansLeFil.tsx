"use client";

/**
 * La **question d'un agent** dans le fil, et le geste qui y répond (#1025).
 *
 * Un agent outillé peut, depuis #1023, suspendre sa tâche pour demander ce qu'il
 * ne sait pas — « Postgres ou SQLite ? » — et reprendre sur une hypothèse
 * annoncée si personne ne répond. Le canal existait des deux côtés ; il
 * n'arrivait nulle part. Cette carte est l'endroit où il arrive.
 *
 * ## Elle n'est pas une validation, et ça se voit d'ici
 *
 * `PanneauValidations` tranche un **acte** par oui/non. Ici on écrit du
 * **texte**, et répondre n'autorise rien : un outil classé `ask` reste refusé
 * sans canal d'arbitrage (EF-08, docs/32 §5). D'où deux canaux, deux files, deux
 * surfaces — et un seul endroit où elles se rejoignent, le **compte** de la
 * cloche, qui répond à « combien de choses m'attendent » (#322).
 *
 * ## La forme vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « ## Veille de conception » et « ## Variante retenue » de #1025 —
 * quatre références capturées, trois variantes rendues sur la vraie stack,
 * jugées par un regard qui n'en était pas l'auteur (#980, #1009). Ce qu'elles
 * tranchent, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **au pied du fil, à la place de la saisie** — la place que la demande de
 *   cadrage occupe déjà (#943), et c'est ce que le premier critère demande en
 *   toutes lettres (« même mécanique que le cadrage »). Deux moments jumeaux ne
 *   devaient pas avoir deux grammaires : même carte `attention`, même en-tête
 *   capitalisé avec son icône et son heure à droite, même libellé au-dessus du
 *   champ, même bouton plein en bas à gauche. La variante qui faisait de la
 *   question un **message du fil** (d'après Zulip, dont le sondage *est* le
 *   message) a été écartée sur sa propre capture : ce qui arrivait après elle se
 *   rangeait dessous et la poussait hors de l'écran — la seule chose qui attend
 *   un humain devenait la seule à pouvoir disparaître ;
 * - **les gestes sont dérivés de ce que l'agent a déclaré** — d'après le
 *   LangChain Agent Inbox, dont le `config` (`allow_respond`, `allow_accept`, …)
 *   décide des actions rendues. Pas de `choix`, pas de boutons : l'écran n'en
 *   fabrique aucun ;
 * - **les choix d'abord, le champ libre ensuite, dans le même bloc** — d'après
 *   Zulip (les options, puis « New option » dans le même widget) et GitHub
 *   Actions (« Optionally, leave a comment » sous la sélection). Jamais un mode
 *   à choisir entre « répondre d'un geste » et « répondre en une phrase » ;
 * - **ce qui se passera sans réponse est écrit avec la question**, sur une ligne
 *   à elle — d'après n8n, dont le « Limit Wait Time » est un réglage de la
 *   demande et non une annexe du geste ;
 * - **la question porte le poids**, juste sous l'en-tête. Elle ne va pas *dans*
 *   l'en-tête, contrairement au cadrage : ce créneau-là rend en petites
 *   capitales, une grammaire d'**étiquette** — le produit n'y met que des noms
 *   de zone courts et fixes —, et une phrase écrite par un modèle y deviendrait
 *   illisible dès qu'elle fait trois lignes. L'en-tête nomme donc ce que la
 *   carte est, la question prend le pas typographique juste en dessous.
 *
 * Refusé, avec sa raison : un **compte à rebours** sur la borne (un rendu par
 * seconde sur un fil qui s'écrit, ce que #877 évite, et `motion-reduce`
 * l'éteindrait) ; une **couleur propre aux questions** (docs/30 §6.1 —
 * `attention` *est* le ton de « quelque chose attend un geste »).
 */

import { useId, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeAide } from "@/components/Icones";
import { BadgeEtat, Bouton, ChampTexte } from "@/components/Primitives";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { questionEchue } from "@/lib/questions";
import type { Question } from "@/lib/types";

export function QuestionDansLeFil({
  question,
  repondre,
}: {
  question: Question;
  /** Porte la réponse à l'agent — le texte, tel qu'il a été écrit ou choisi. */
  repondre: (questionId: string, reponse: string) => Promise<void>;
}) {
  const maintenant = useHorloge();
  const [ecrite, setEcrite] = useState("");
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<string | null>(null);

  // L'agent est-il déjà reparti ? La question reste **répondable** dans les deux
  // cas — une réponse tardive sert encore (`MemoireArbitrage`, #584) —, seul
  // change ce que la carte dit de l'attente (`lib/questions`).
  const echue = questionEchue(question, maintenant);
  const retenue = ecrite.trim();
  // Un identifiant unique **par carte montée**, et non par question (#1106).
  // Deux champs de même `id` dans un document feraient perdre son nom
  // accessible au second (le refus écrit de `CadreChamp`) — et depuis que la
  // colonne de droite porte les gestes du fil, la **même** question s'affiche
  // deux fois sur un même écran : le fil de l'orchestration les porte toutes,
  // l'onglet Chat d'une fiche agent porte les siennes, et les deux sont montés
  // ensemble dès qu'on ouvre la colonne sur `/agents`. `question_id` ne
  // distinguait pas ces deux montages ; `useId` les distingue par
  // construction, et distingue toujours deux questions d'une même tâche.
  const idCarte = useId();
  const idChamp = `question-${idCarte}`;

  const envoyer = async (reponse: string) => {
    if (reponse === "" || enCours) return;
    setEnCours(true);
    setRefus(null);
    try {
      await repondre(question.question_id, reponse);
      // Succès : la question sort de l'attente au rechargement et la carte se
      // démonte — inutile de rétablir `enCours`. En cas d'échec seulement, on
      // rend la main pour réessayer.
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    /* « Question de l'agent <nom> », et non « Question de <nom> » : le nom
       d'un agent est déclaré par l'équipe d'un projet, donc arbitraire
       (`Question.agent`), et « Question de infra » y manquait son élision
       (#1110). Des deux issues que le ticket ouvre, celle-ci est la seule
       qui tienne pour **tous** les noms : l'élision française se décide à
       l'oreille et non à la lettre, si bien qu'une règle dérivée de
       l'initiale se tromperait au premier sigle, h muet ou « u »
       semi-voyelle — on supprime la classe de coquilles au lieu de la
       rétrécir. Le titre dit du même coup exactement ce que l'`aria-label`
       de la carte annonce. */
    <CarteDuFil
      libelle={`Question de l'agent ${question.agent}`}
      icone={IconeAide}
      titre={`Question de l'agent ${question.agent}`}
      aside={
        echue ? (
          // L'état ne tient pas à la couleur seule (docs/30 §1.6) : il est
          // écrit — ici en badge, et en toutes lettres au pied de la carte.
          <BadgeEtat ton="attention">Reparti sans réponse</BadgeEtat>
        ) : question.horodatage ? (
          <span className="text-annexe text-texte-secondaire">
            demandé {formatHeureRelative(question.horodatage, maintenant)}
          </span>
        ) : undefined
      }
    >
      {/* Qui demande et à propos de quoi, en une ligne et en place fixe —
          d'après la table « Event · Environments · Comment » de GitHub Actions
          et le troisième manque du banc de #471. Le rôle peut manquer sur une
          question venue d'ailleurs : la ligne se resserre plutôt que d'afficher
          un séparateur qui ne sépare rien. */}
      <p className="mb-3 text-annexe text-texte-secondaire">
        {[question.role, question.titre].filter(Boolean).join(" · ")}
      </p>
      {/* La question porte le poids (voir l'en-tête du fichier). `whitespace-pre-wrap`
          parce qu'elle vient d'un modèle : ses retours à la ligne sont les siens,
          et les écraser recomposerait ce qu'il a écrit. */}
      <p className="text-titre font-semibold whitespace-pre-wrap text-texte">
        {question.question}
      </p>
      {/* Les gestes que l'agent a déclarés, et eux seuls. Une liste plutôt que
          des boutons nus : ce sont des options, donc un ensemble, et un lecteur
          d'écran en annonce le compte. */}
      {question.choix.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {question.choix.map((choix) => (
            <li key={choix}>
              <Bouton
                variante="contour"
                ton="neutre"
                disabled={enCours}
                onClick={() => void envoyer(choix)}
              >
                {choix}
              </Bouton>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-3">
        <ChampTexte
          id={idChamp}
          libelle={
            question.choix.length > 0
              ? "Ou répondez en une phrase"
              : "Votre réponse"
          }
          value={ecrite}
          onChange={(e) => setEcrite(e.target.value)}
          disabled={enCours}
          rows={2}
        />
      </div>
      <div className="mt-3">
        <Bouton
          disabled={retenue === ""}
          occupe={enCours}
          onClick={() => void envoyer(retenue)}
        >
          Répondre
        </Bouton>
      </div>
      {/* Ce qui se passera — ou ce qui s'est passé — sans réponse, sur sa ligne.
          Avant la borne, la phrase est celle que l'API compose (`attente`,
          docs/05 §6.17) et elle est rendue **telle quelle** : elle porte déjà le
          délai, et la recomposer ici ferait deux rédactions du même fait. Après
          la borne, c'est l'écran qui parle, parce que le canal ne dit rien de
          plus — la question reste ouverte et l'agent, lui, est reparti. */}
      <p className="mt-3 text-annexe text-attention-texte">
        {echue ? (
          <>
            Personne n&apos;a répondu à temps : l&apos;agent est reparti sur son
            hypothèse — <strong className="font-medium">{question.hypothese}</strong>.
            Répondre sert encore, il la retrouvera au prochain appel identique.
          </>
        ) : question.attente !== "" ? (
          // Première lettre en capitale sans toucher au texte : la phrase de
          // l'API commence par « sans réponse… » parce qu'elle est composée pour
          // être lue au milieu d'une ligne de journal.
          <>
            {question.attente.charAt(0).toLocaleUpperCase("fr")}
            {question.attente.slice(1)}.
          </>
        ) : (
          <>
            Sans réponse, l&apos;agent reprendra sur son hypothèse :{" "}
            <strong className="font-medium">{question.hypothese}</strong>.
          </>
        )}
      </p>
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}

/**
 * Les questions d'un fil, empilées au pied — la plus ancienne en haut.
 *
 * Une carte par question, et **aucune borne** : deux questions d'une même tâche
 * peuvent attendre ensemble (docs/05 §6.17), et en replier une derrière un
 * « voir les autres » ferait exactement ce que le canal existe pour éviter — une
 * demande posée que personne ne voit. Si la pile devient un problème de hauteur,
 * c'est une mesure (`/banc-mise-en-page`), pas un pli à ajouter ici.
 *
 * L'ordre est celui de `questionsEnAttente` : la plus ancienne d'abord, comme la
 * file des briefs (`runsEnAttente`, `lib/brief`) — c'est l'ancienneté qui
 * distingue « à traiter » de « en train d'être traité ».
 */
export function QuestionsDuFil({
  questions,
  repondre,
}: {
  questions: Question[];
  repondre: (questionId: string, reponse: string) => Promise<void>;
}) {
  if (questions.length === 0) return null;
  return (
    <div className="flex flex-col gap-3">
      {questions.map((question) => (
        <QuestionDansLeFil
          key={question.question_id}
          question={question}
          repondre={repondre}
        />
      ))}
    </div>
  );
}

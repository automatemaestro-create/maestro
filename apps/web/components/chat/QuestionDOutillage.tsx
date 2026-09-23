"use client";

/**
 * La question d'outillage du fil, **avec le geste qui y répond** (#1031).
 *
 * Maestro pose à l'utilisateur les questions qui décident de l'outillage d'un projet
 * neuf — sa nature, son langage, ses tests, sa forge, sa CI, ses conventions. Chacune
 * **propose une recommandation** et se répond d'un geste, au pied du fil, **sans
 * formulaire à part** : c'est le critère du ticket, et c'est pourquoi cette carte vit
 * dans la conversation et non dans un écran de réglages.
 *
 * ## La mécanique n'est pas inventée ici : c'est celle du cadrage (#1014)
 *
 * Même place (`Conversation.pied`, là où l'œil vient de lire la question et où la
 * main allait taper), même `Carte ton="attention"`, même « un échange en vol désarme
 * les gestes ». Ce qui change est ce qu'on répond : un objectif se **corrige**, une
 * question se **choisit**.
 *
 * ⚠ Ce composant ne juge pas si la question attend encore : c'est une propriété de la
 * **suite** des messages, pas de l'un d'eux, et elle s'énonce une seule fois
 * (`questionEnAttente`, `lib/outillage`). Il reçoit la question ou rien.
 *
 * ## La forme vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1031 : trois
 * variantes rendues sur la vraie stack, jugées par un regard qui n'en était pas
 * l'auteur (#980). La retenue est **A — toutes les options ouvertes, la recommandée
 * en tête**. Ce qu'elle tranche, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **on montre tout le choix avant le geste** — d'après le sondage de Slack Block
 *   Kit, où la question et ses options vivent dans le message. La variante qui
 *   repliait les alternatives derrière un contrôle (d'après Vercel, dont le réglage
 *   détecté s'applique et dont l'override est le geste supplémentaire) a été écartée
 *   sur pièces : dépliée, la liste passait **sous la zone de saisie**, et la
 *   troisième option n'était visible nulle part ;
 * - **chaque option porte sa propre raison**, sous son libellé — Slack encore, où
 *   c'est la ligne d'explication qui distingue deux options du même rang. La variante
 *   qui mettait les options dans un `ChampListe` a été écartée : un `select` natif
 *   n'a pas de place pour une raison, donc rien n'y disait plus *pourquoi* une
 *   alternative existe ;
 * - **la recommandée est remontée en tête, présélectionnée, et porte son badge** —
 *   d'après Vercel (« sets the best settings for you ») : le bouton est armé dès
 *   l'affichage, et changer d'option ne demande rien de plus que la choisir ;
 * - **la sélection se porte par le ton `accent` ET par une coche**, jamais par la
 *   couleur seule. C'est une correction du regard neuf : le brouillon marquait la
 *   ligne retenue d'un aplat `selectionne` (gris), seul élément gris d'une carte
 *   ambrée, qui se lisait « désactivé » plutôt que « retenu ». C'est aussi ce que
 *   docs/30 §1 demande — l'état porte une forme autant qu'une couleur ;
 * - **les raisons tiennent en une ligne.** Seconde correction du regard neuf : c'est
 *   la carte la plus haute des trois, et six questions d'affilée à ce format
 *   chasseraient le fil de l'écran. L'épuration passe par les raisons, **jamais par
 *   le retrait d'une option** — ce serait retirer précisément ce que la variante a
 *   été retenue pour montrer.
 *
 * Le rang (« question 3 sur 6 ») reste en tête : les deux variantes qui le gardaient
 * ont été jugées meilleures que celle qui l'avait perdu, et `total` est un **plafond**
 * — le questionnaire peut finir avant, les questions déduites n'étant pas posées.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeCoche, IconeObjectif } from "@/components/Icones";
import { BadgeEtat, Bouton, classesCarte } from "@/components/Primitives";
import type { QuestionOutillage } from "@/lib/types";

export function QuestionDOutillage({
  question,
  repondre,
  enCours = false,
}: {
  /** La question que le fil porte encore — `question.recommande` est la proposition. */
  question: QuestionOutillage;
  /** Le geste : l'option retenue part, la suite du questionnaire revient. */
  repondre: (valeur: string) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : le geste se désarme. */
  enCours?: boolean;
}) {
  const [choisi, setChoisi] = useState(question.recommande);
  const [refus, setRefus] = useState<string | null>(null);

  // La recommandée en tête, le reste dans l'ordre du catalogue. Trier sur la
  // **recommandation** et non sur la sélection courante : une liste qui se
  // réordonne sous le pointeur fait cliquer à côté.
  const ordonnees = [
    ...question.options.filter((o) => o.valeur === question.recommande),
    ...question.options.filter((o) => o.valeur !== question.recommande),
  ];

  const surReponse = async () => {
    setRefus(null);
    try {
      await repondre(choisi);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Question d'outillage"
      icone={IconeObjectif}
      titre={question.intitule}
      aside={
        <span className="text-annexe text-texte-secondaire">
          question {question.rang} sur {question.total}
        </span>
      }
    >
      <div
        role="radiogroup"
        aria-label={question.intitule}
        className="flex flex-col gap-2"
      >
        {ordonnees.map((option) => {
          const actif = option.valeur === choisi;
          const recommandee = option.valeur === question.recommande;
          return (
            <button
              key={option.valeur}
              type="button"
              role="radio"
              aria-checked={actif}
              disabled={enCours}
              onClick={() => setChoisi(option.valeur)}
              // `classesCarte` plutôt qu'un padding et un rayon écrits à la main :
              // c'est le recours prévu pour ce qui ne peut pas être une `Carte`
              // (un `<button>`, dont `type` et `disabled` ne vivent pas dans
              // `HTMLAttributes`), et c'est ce qui garde le barème à trois pas.
              //
              // La ligne **retenue** prend `attentionClaire` — le ton fait pour une
              // carte d'arbitrage posée dans une zone `attention` : elle garde le
              // fond du contenu, donc elle se **détache** de l'ambre. Les autres
              // prennent `attention`, donc restent au ras de la zone. C'est la
              // correction du regard neuf, qui lisait « désactivé » sur l'aplat gris
              // du brouillon : ici le choix est ce qui se soulève, pas ce qui pâlit.
              className={classesCarte({
                densite: "compacte",
                ton: actif ? "attentionClaire" : "attention",
                className:
                  "flex min-w-0 items-start gap-2 text-left" +
                  (actif ? "" : " hover:bg-survol"),
              })}
            >
              {/* La **forme** du choix, à côté de la surface : une coche, ou la
                  place qu'elle occupe, pour que les lignes restent alignées quelle
                  que soit la sélection. L'état ne tient donc jamais à la seule
                  couleur (docs/30 §1). */}
              <IconeCoche
                aria-hidden
                className={
                  "mt-0.5 size-4 shrink-0 " +
                  (actif ? "text-accent-texte" : "invisible")
                }
              />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex flex-wrap items-center gap-2">
                  <span className="text-corps font-medium text-texte">
                    {option.libelle}
                  </span>
                  {recommandee && <BadgeEtat ton="info">Recommandé</BadgeEtat>}
                </span>
                {/* La raison de l'option, sur **toutes** les lignes — c'est elle
                    qui dit ce que ce choix entraîne, et c'est par elle qu'on
                    compare deux options.

                    ⚠ La recommandée en porte une **seconde**, et non une autre :
                    le premier jet y mettait `pourquoi` **à la place** de
                    `raison`, si bien que la seule option qu'on ne pouvait pas
                    lire par sa ligne était celle qu'on recommandait (constat du
                    regard neuf, #980). Les deux phrases ne répondent pas à la
                    même question — « qu'est-ce que ce choix ? » et « pourquoi
                    celui-là ? » —, donc aucune ne remplace l'autre. */}
                <span className="min-w-0 break-words text-annexe text-texte-secondaire">
                  {option.raison}
                </span>
                {recommandee && (
                  <span className="min-w-0 break-words text-annexe text-attention-texte">
                    {question.pourquoi}
                  </span>
                )}
              </span>
            </button>
          );
        })}
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Bouton occupe={enCours} onClick={() => void surReponse()}>
          {choisi === question.recommande
            ? "Garder ce choix"
            : "Retenir ce choix"}
        </Bouton>
      </div>
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}

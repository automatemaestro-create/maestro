"use client";

/**
 * La question d'outillage d'un projet neuf, **avec le geste qui y répond** (#1031,
 * #1147).
 *
 * Maestro comprend le projet à partir de ce que la personne en dit, puis ne demande
 * que ce qui manque — chaque question avec des options **écrites pour ce projet**, et
 * une recommandation. Elle se répond d'un geste, au pied du fil ou dans l'étape
 * « Outillage » de la création, **sans formulaire à part** : c'est pourquoi cette
 * carte vit dans la conversation et non dans un écran de réglages.
 *
 * ## La mécanique n'est pas inventée ici : c'est celle du cadrage (#1014)
 *
 * Même place (`Conversation.pied`, là où l'œil vient de lire la question et où la
 * main allait taper), même carte, même « un échange en vol désarme les gestes ». Ce
 * qui change est ce qu'on répond : un objectif se **corrige**, une question se
 * **choisit** — ou se répond avec ses mots.
 *
 * ⚠ Ce composant ne juge pas si la question attend encore : c'est une propriété de la
 * **suite** des messages, pas de l'un d'eux, et elle s'énonce une seule fois
 * (`questionEnAttente`, `lib/outillage`). Il reçoit la question ou rien.
 *
 * ## La forme vient de deux veilles et de deux choix rendus sur pièces
 *
 * **#1031** — commentaires « Veille de conception » et « Variante retenue » de #1031 :
 * la retenue est **A — toutes les options ouvertes, la recommandée en tête**. Ce
 * qu'elle tranche, et qui tient :
 *
 * - **on montre tout le choix avant le geste** — d'après le sondage de Slack Block
 *   Kit, où la question et ses options vivent dans le message ;
 * - **chaque option porte sa propre raison**, sous son libellé, en une ligne ;
 * - **la recommandée est remontée en tête, présélectionnée, et porte son badge** —
 *   d'après Vercel (« sets the best settings for you ») : le bouton est armé dès
 *   l'affichage ;
 * - **la sélection se porte par un ton ET par une coche**, jamais par la couleur
 *   seule (docs/30 §1) — correction du regard neuf de #1031.
 *
 * **#1147** — un projet d'une sorte qu'aucune liste ne prévoyait (« p2 ») n'avait
 * aucune réponse honnête. Commentaires « Veille de conception » et « Variante
 * retenue » de #1147 : la retenue est **C — les options, puis « Autre chose » qui
 * ouvre le champ**, choisie par le regard neuf contre A (un champ toujours visible,
 * qui laissait une option cochée perdre sans le dire contre le texte tapé) et B (le
 * champ en tête et les options en pastilles, qui pliait tout ce que #1031 a tranché).
 * Ce qu'elle tranche :
 *
 * - **la réponse libre est une option de plus, la dernière** — d'après le contrat
 *   `AskUserQuestion` du Claude Agent SDK (« display an additional "Other" choice
 *   after Claude's options that accepts text input ; use the user's custom text as
 *   the answer value »). Une seule réponse est sélectionnée à la fois ;
 * - **une question sans options — la première, « Qu'est-ce que ce projet ? » — ne
 *   montre que le champ**, jamais un « Autre chose » isolé : on commence par dire son
 *   projet avec ses mots, d'après *Bolt* ;
 * - **le champ a un libellé visible**, pas seulement un texte indicatif qui disparaît
 *   dès qu'on tape ;
 * - **au pied du fil, la carte dit que la zone de saisie répond aussi**, d'après
 *   *Intercom* (« customers can still write a reply in the composer ») : une phrase
 *   tapée là est enregistrée comme la réponse à cette question ;
 * - **« Ce que j'ai compris » se lit en tête** : c'est ce qui répond à « Maestro
 *   l'a-t-il compris ? », et ce qu'on corrige avec ses mots s'il se trompe ;
 * - **plus de « question N sur 6 »** : le plafond fixe est retiré, le questionnaire
 *   s'arrête quand plus rien ne manque, et un total que personne ne connaît d'avance
 *   mentirait.
 */

import { useId, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeCoche, IconeObjectif } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  ChampTexte,
  classesCarte,
} from "@/components/Primitives";
import type { ChoixOutillage, QuestionOutillage } from "@/lib/types";

/** La sélection « Autre chose » — une valeur qu'aucune option générée ne porte. */
const AUTRE = "\u0000autre";

/** Ce que Maestro a compris, en une ligne : « tests : flutter test · forge : github ». */
function enPhrase(compris: ChoixOutillage[]): string {
  return compris.map((c) => `${c.sujet ?? c.cle} : ${c.valeur}`).join(" · ");
}

export function QuestionDOutillage({
  question,
  repondre,
  compris = [],
  depuisLeFil = false,
  enCours = false,
}: {
  /** La question que le fil porte encore — `question.recommande` est la proposition. */
  question: QuestionOutillage;
  /**
   * Le geste : l'option retenue part — ou, `libre`, la réponse avec ses mots —, et
   * la suite du questionnaire revient.
   */
  repondre: (valeur: string, libre: boolean) => Promise<void>;
  /** Ce que Maestro a compris du projet jusqu'ici (#1147) — vide : rien encore. */
  compris?: ChoixOutillage[];
  /**
   * La carte est au pied d'une conversation : la zone de saisie, juste dessous,
   * répond aussi à la question, et la carte le dit.
   */
  depuisLeFil?: boolean;
  /** Un échange est déjà en vol sur ce fil : le geste se désarme. */
  enCours?: boolean;
}) {
  const ouverte = question.options.length === 0;
  const [choisi, setChoisi] = useState(ouverte ? AUTRE : question.recommande);
  const [libre, setLibre] = useState("");
  const [refus, setRefus] = useState<string | null>(null);
  const idChamp = useId();

  // La recommandée en tête, le reste dans l'ordre où Maestro les a écrites. Trier
  // sur la **recommandation** et non sur la sélection courante : une liste qui se
  // réordonne sous le pointeur fait cliquer à côté.
  const ordonnees = [
    ...question.options.filter((o) => o.valeur === question.recommande),
    ...question.options.filter((o) => o.valeur !== question.recommande),
  ];
  const avecSesMots = choisi === AUTRE;
  const texte = libre.trim();

  const surReponse = async () => {
    setRefus(null);
    try {
      if (avecSesMots) await repondre(texte, true);
      else await repondre(choisi, false);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  const libelleDuGeste = avecSesMots
    ? "Envoyer ma réponse"
    : choisi === question.recommande
      ? "Garder ce choix"
      : "Retenir ce choix";

  return (
    <CarteDuFil
      libelle="Question d'outillage"
      icone={IconeObjectif}
      titre={question.intitule}
    >
      {compris.length > 0 && (
        <p className="mb-3 min-w-0 break-words text-annexe text-texte-secondaire">
          <span className="font-medium text-texte">
            Ce que j&apos;ai compris :
          </span>{" "}
          {enPhrase(compris)}
        </p>
      )}
      {ouverte ? (
        <p className="mb-2 min-w-0 break-words text-annexe text-texte-secondaire">
          {question.pourquoi}
        </p>
      ) : (
        <div
          role="radiogroup"
          aria-label={question.intitule}
          className="flex flex-col gap-2"
        >
          {ordonnees.map((option) => (
            <LigneDeChoix
              key={option.valeur}
              actif={option.valeur === choisi}
              libelle={option.libelle}
              raison={option.raison}
              badge={option.valeur === question.recommande}
              pourquoi={
                option.valeur === question.recommande ? question.pourquoi : ""
              }
              desarme={enCours}
              choisir={() => setChoisi(option.valeur)}
            />
          ))}
          <LigneDeChoix
            actif={avecSesMots}
            libelle="Autre chose"
            raison="Dites-le avec vos mots — Maestro en tirera la suite."
            desarme={enCours}
            choisir={() => setChoisi(AUTRE)}
          />
        </div>
      )}
      {avecSesMots && (
        <ChampTexte
          id={idChamp}
          libelle={ouverte ? "Votre réponse" : "Votre réponse, avec vos mots"}
          className={ouverte ? "" : "mt-2"}
          rows={2}
          value={libre}
          disabled={enCours}
          onChange={(e) => setLibre(e.target.value)}
          placeholder="Une application mobile Flutter, une infrastructure Terraform…"
        />
      )}
      <div className="mt-4 flex flex-wrap items-center gap-x-3 gap-y-2">
        <Bouton
          occupe={enCours}
          disabled={avecSesMots && texte === ""}
          onClick={() => void surReponse()}
        >
          {libelleDuGeste}
        </Bouton>
        {depuisLeFil && (
          <span className="text-annexe text-texte-secondaire">
            Vous pouvez aussi répondre dans la zone de saisie, ci-dessous.
          </span>
        )}
      </div>
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}

/**
 * Une ligne du choix : un bouton `radio`, sa coche, son libellé et sa raison.
 *
 * `classesCarte` plutôt qu'un padding et un rayon écrits à la main : c'est le recours
 * prévu pour ce qui ne peut pas être une `Carte` (un `<button>`, dont `type` et
 * `disabled` ne vivent pas dans `HTMLAttributes`), et c'est ce qui garde le barème à
 * trois pas.
 *
 * La ligne **retenue** prend `attentionClaire` — elle garde le fond du contenu, donc
 * se **détache** de l'ambre ; les autres restent au ras de la zone (`attention`).
 * C'est la correction du regard neuf de #1031 : le choix est ce qui se soulève, pas
 * ce qui pâlit. La **coche** (ou la place qu'elle occupe) porte la même information
 * par la forme, pour que l'état ne tienne jamais à la seule couleur.
 */
function LigneDeChoix({
  actif,
  libelle,
  raison,
  badge = false,
  pourquoi = "",
  desarme,
  choisir,
}: {
  actif: boolean;
  libelle: string;
  raison: string;
  /** La ligne est la recommandation de Maestro. */
  badge?: boolean;
  /** Ce qui, dans ce projet, désigne la recommandation — sur sa seule ligne. */
  pourquoi?: string;
  desarme: boolean;
  choisir: () => void;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={actif}
      disabled={desarme}
      onClick={choisir}
      className={classesCarte({
        densite: "compacte",
        ton: actif ? "attentionClaire" : "attention",
        className:
          "flex min-w-0 items-start gap-2 text-left" +
          (actif ? "" : " hover:bg-survol"),
      })}
    >
      <IconeCoche
        aria-hidden
        className={
          "mt-0.5 size-4 shrink-0 " + (actif ? "text-accent-texte" : "invisible")
        }
      />
      <span className="flex min-w-0 flex-col gap-0.5">
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-corps font-medium text-texte">{libelle}</span>
          {badge && <BadgeEtat ton="info">Recommandé</BadgeEtat>}
        </span>
        {/* La raison de l'option, sur **toutes** les lignes — c'est elle qui dit ce
            que ce choix entraîne. La recommandée en porte une **seconde**, et non
            une autre : « qu'est-ce que ce choix ? » et « pourquoi celui-là ? » ne
            répondent pas à la même question (constat du regard neuf, #980). */}
        {raison !== "" && (
          <span className="min-w-0 break-words text-annexe text-texte-secondaire">
            {raison}
          </span>
        )}
        {pourquoi !== "" && (
          <span className="min-w-0 break-words text-annexe text-attention-texte">
            {pourquoi}
          </span>
        )}
      </span>
    </button>
  );
}

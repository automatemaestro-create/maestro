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
 *   l'a-t-il compris ? », et ce qu'on corrige avec ses mots s'il se trompe. Une
 *   entrée par constat, la valeur en relief — correction du regard neuf de la
 *   relecture, qui lisait un seul flot de clés et de valeurs à égalité ;
 * - **une option montre ce qu'elle écrira** (`flutter build apk`) quand son nom
 *   (« Android uniquement ») ne le dit pas — seconde correction de la même relecture ;
 * - **plus de « question N sur 6 »** : le plafond fixe est retiré, le questionnaire
 *   s'arrête quand plus rien ne manque, et un total que personne ne connaît d'avance
 *   mentirait.
 */

import { useEffect, useId, useRef, useState } from "react";

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

/**
 * Jusqu'où « Ce que j'ai compris » reste déplié même dans une carte étroite : trois
 * entrées tiennent en deux lignes dans la colonne de conversation (320 px), sans
 * repousser le titre de la question hors du fil (voir `ComprisDuProjet`).
 */
const COMPRIS_DEPLIE_MAX = 3;

/** Un nom ou une valeur réduits à leurs lettres et chiffres — de quoi les comparer. */
function reduit(texte: string): string {
  return texte.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/**
 * La valeur concrète d'une option, **quand son nom ne la dit pas déjà** (#1147).
 *
 * Le modèle écrit la valeur (ce qui sera écrit dans l'outillage : `flutter build
 * apk`, `.github/workflows/ci.yml`) et un nom pour la lire (« Android uniquement »,
 * « GitHub Actions »). Constat du regard neuf sur la vraie stack : « Quelle commande
 * de build ? » coiffait trois plateformes, et la commande qui serait écrite n'était
 * lisible nulle part. Quand le nom la redit (« GitHub » / `github`), on la tait.
 *
 * « aucun » aussi : ce n'est pas une valeur à lire, et le nom dit déjà qu'il n'y a
 * rien (troisième relecture : un « aucun » gris à la place d'une commande).
 */
function valeurDite(valeur: string, libelle: string): string | null {
  const brute = reduit(valeur);
  return brute === reduit(libelle) || brute === "aucun" || brute === "aucune"
    ? null
    : valeur;
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

  // Choisir « Autre chose » agrandit la carte d'un champ, **sous** le bas du fil :
  // mesuré sur la vraie stack, le bouton d'envoi passait alors sous la zone de
  // saisie tant qu'on ne faisait pas défiler. On amène le geste sous les yeux — le
  // plus court déplacement (`nearest`), rien si tout est déjà visible. La question
  // ouverte n'en a pas besoin : son champ est là dès l'affichage.
  const gestes = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (avecSesMots && !ouverte) {
      gestes.current?.scrollIntoView?.({ block: "nearest" });
    }
  }, [avecSesMots, ouverte]);

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
        <ComprisDuProjet compris={compris} id={`${idChamp}-compris`} />
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
              valeur={valeurDite(option.valeur, option.libelle)}
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
      {/* `scroll-mb-44` : au pied du fil, le composeur est `sticky` **dans** le même
          ascenseur (`Conversation`, à quai à 64 px du bas quand le flottant passe),
          si bien que le navigateur tient pour visible un bouton qu'il recouvre.
          La marge — la hauteur du composeur et de sa bande, arrondie au-dessus —
          fait remonter le geste au-dessus de lui. Hors du fil (l'étape de
          création), elle ne coûte qu'un défilement un peu plus généreux. */}
      <div
        ref={gestes}
        className="mt-4 flex scroll-mb-44 flex-wrap items-center gap-x-3 gap-y-2"
      >
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
 * « Ce que j'ai compris » — une entrée par constat, le sujet en retrait et la valeur
 * en relief, jamais coupés l'un de l'autre d'une ligne à la suivante.
 *
 * C'est la correction du regard neuf de la relecture de #1147, qui lisait un seul
 * flot de clés et de valeurs à égalité (« types » en fin de ligne, « : aucun » sur la
 * suivante). Une entrée passe à la ligne **entière** ; seule une valeur plus large
 * que la carte se coupe, à l'intérieur d'elle-même.
 *
 * ⚠ **Dans une carte étroite, elle passe à un second niveau** (seconde relecture) :
 * dans la colonne de conversation (320 px), la carte dépassait la hauteur du fil, et
 * le fil, calé en bas, poussait hors du champ le titre de la question — la seule
 * chose qu'il faut y lire d'abord. Sous le seuil `@md` du **conteneur** (la carte,
 * pas la fenêtre : la même carte vit au pied de `/chat` et dans la colonne), la liste
 * se replie derrière un `<details>` qui en dit le compte. C'est le recours de docs/30
 * §4 — un second niveau, **jamais un retrait d'information**. Au-dessus du seuil, la
 * liste est dépliée, sans geste.
 *
 * ⚠ **Seulement quand elle est longue** (troisième relecture) : un seul constat replié
 * dans la colonne, lu ouvert au même moment sur `/chat`, faisait lire la même carte de
 * deux façons — pour une ligne qui ne menaçait rien. Jusqu'à `COMPRIS_DEPLIE_MAX`
 * entrées, la liste est dépliée partout.
 */
function ComprisDuProjet({
  compris,
  id,
}: {
  compris: ChoixOutillage[];
  id: string;
}) {
  // Une commande en chasse fixe, comme la valeur d'une option : en romain gras,
  // « dart format . » se lisait comme une fin de phrase (troisième relecture).
  const entrees = compris.map((c) => (
    <li key={c.cle} className="min-w-0 break-words">
      <span className="text-texte-secondaire">{c.sujet ?? c.cle}</span>{" "}
      {c.commande ? (
        <code className="font-mono text-texte">{c.valeur}</code>
      ) : (
        <span className="font-medium text-texte">{c.valeur}</span>
      )}
    </li>
  ));
  const liste = "flex min-w-0 flex-wrap gap-x-4 gap-y-1 text-annexe";
  if (compris.length <= COMPRIS_DEPLIE_MAX) {
    return (
      <div className="mb-3 flex min-w-0 flex-col gap-1">
        <p id={id} className="text-annexe font-medium text-texte">
          Ce que j&apos;ai compris
        </p>
        <ul aria-labelledby={id} className={liste}>
          {entrees}
        </ul>
      </div>
    );
  }
  return (
    <div className="@container mb-3 min-w-0">
      <div className="hidden min-w-0 flex-col gap-1 @md:flex">
        <p id={id} className="text-annexe font-medium text-texte">
          Ce que j&apos;ai compris
        </p>
        <ul aria-labelledby={id} className={liste}>
          {entrees}
        </ul>
      </div>
      <details className="min-w-0 @md:hidden">
        <summary className="cursor-pointer text-annexe font-medium text-texte">
          Ce que j&apos;ai compris · {compris.length} constat
          {compris.length > 1 ? "s" : ""}
        </summary>
        <ul aria-label="Ce que j'ai compris" className={`mt-1 ${liste}`}>
          {entrees}
        </ul>
      </details>
    </div>
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
  valeur = null,
  raison,
  badge = false,
  pourquoi = "",
  desarme,
  choisir,
}: {
  actif: boolean;
  libelle: string;
  /** Ce que l'option écrira, quand son nom ne le dit pas (`valeurDite`). */
  valeur?: string | null;
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
          {valeur !== null && (
            <code className="min-w-0 break-words font-mono text-annexe text-texte-secondaire">
              {valeur}
            </code>
          )}
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

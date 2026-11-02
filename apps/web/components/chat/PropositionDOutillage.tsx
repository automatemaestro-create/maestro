"use client";

/**
 * La **proposition de l'outillage entier** d'un projet neuf, avec le geste qui la
 * retient (#1533).
 *
 * Sur p5, le fil posait neuf questions une à une — langage, gestionnaire, cadre,
 * tests, vérification, formatage, forge, CI, convention de commit —, chacune sa carte
 * (`QuestionDOutillage`) et son geste, pour un outillage Next.js que le modèle savait
 * juger d'un bloc. Il le **propose** désormais en une carte : tout ce qu'il retiendrait
 * pour cette pile, chaque élément avec sa raison. La personne le retient d'un geste, ou
 * le corrige avec ses mots dans la zone de saisie juste dessous (« pnpm, pas npm ») —
 * la phrase répond à la proposition en attente, comme à toute question d'outillage
 * (#1147), et ce qui revient est une proposition corrigée, revérifiée.
 *
 * ⚠ **Retenir n'écrit rien** (#1161) : la proposition retenue est ce que les pièces
 * rédigent, et chacune se montre ensuite avec son diff, écrite sur accord. C'est
 * pourquoi le bouton dit « Retenir », pas « Écrire ».
 *
 * ## La forme vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1533 : la retenue
 * est **A — deux niveaux**, choisie par le regard neuf contre B (le résumé en
 * pastilles, le détail derrière un dépli : commandes et raisons repliées) et C (une
 * seule liste de tout l'outillage marquée « Proposé » ligne à ligne : la plus haute,
 * son geste hors du champ). Ce qu'elle tranche :
 *
 * - **tout est sous les yeux, un seul geste** — d'après la carte *Task plan created*
 *   de Replit (« Build here ») et la carte de projet de #1294 ;
 * - **une ligne par sujet** : le sujet en retrait, le nom en relief, la commande en
 *   chasse fixe quand le nom ne la dit pas (`valeurDite`) — d'après les *Framework
 *   Settings* de Vercel, sans leur interrupteur ni leur champ : corriger se dit dans
 *   la conversation ;
 * - **chaque raison visible**, sous sa valeur, jamais en infobulle ; un outil que le
 *   poste n'a pas se dit sur la même ligne (#1343) ;
 * - **ce qui a été dit et ce qui est proposé se distinguent par la forme** : le
 *   compris en tête, compact (`ComprisDuProjet`), le proposé en liste dessous.
 *
 * ⚠ **Dans une carte étroite, chaque élément passe sur deux lignes** — le sujet
 * au-dessus de sa valeur. Sous le seuil `@md` du **conteneur** (la carte, pas la
 * fenêtre : la même carte vit au pied de `/chat` et dans la colonne de 320 px), deux
 * colonnes laissaient à la valeur et à sa raison la moitié d'une largeur déjà étroite.
 *
 * Ce composant ne juge pas si la proposition attend encore : c'est la règle de toute
 * question d'outillage (`questionEnAttente`, `lib/outillage`). Il la reçoit, ou rien.
 */

import { useId, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { ComprisDuProjet, valeurDite } from "@/components/chat/QuestionDOutillage";
import { IconeObjectif } from "@/components/Icones";
import { Bouton } from "@/components/Primitives";
import { PROPOSITION_RETENUE } from "@/lib/outillage";
import type {
  ChoixOutillage,
  ElementProposeOutillage,
  QuestionOutillage,
} from "@/lib/types";

export function PropositionDOutillage({
  question,
  repondre,
  compris = [],
  enCours = false,
}: {
  /** La proposition que le fil porte encore — `question.proposition` en est le contenu. */
  question: QuestionOutillage;
  /** Le geste : la proposition retenue part (`PROPOSITION_RETENUE`), la suite revient. */
  repondre: (valeur: string, libre: boolean) => Promise<void>;
  /** Ce que Maestro a compris de ce qui a été dit — vide : rien encore. */
  compris?: ChoixOutillage[];
  /** Un échange est déjà en vol sur ce fil : le geste se désarme. */
  enCours?: boolean;
}) {
  const [refus, setRefus] = useState<string | null>(null);
  const id = useId();
  const elements = question.proposition ?? [];

  const retenir = async () => {
    setRefus(null);
    try {
      await repondre(PROPOSITION_RETENUE, false);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Proposition d'outillage"
      icone={IconeObjectif}
      titre={question.intitule}
    >
      {compris.length > 0 && <ComprisDuProjet compris={compris} id={`${id}-compris`} />}
      <div className="@container min-w-0">
        <p id={`${id}-propose`} className="mb-2 text-annexe font-medium text-texte">
          Ce que je propose
        </p>
        <dl
          aria-labelledby={`${id}-propose`}
          className="flex min-w-0 flex-col gap-2 text-corps @md:grid @md:grid-cols-[auto_minmax(0,1fr)] @md:gap-x-3 @md:gap-y-1.5"
        >
          {elements.map((element) => (
            <ElementPropose key={element.cle} element={element} />
          ))}
        </dl>
      </div>
      <p className="mt-3 text-annexe text-texte-secondaire">
        Quelque chose à changer ? Dites-le juste en dessous, avec vos mots —
        « pnpm, pas npm », « pas de CI pour l&apos;instant ».
      </p>
      {/* `scroll-mb-44` : la même raison que sur la carte de question — au pied du
          fil, le composeur collant recouvre ce que le navigateur tient pour visible. */}
      <div className="mt-3 flex scroll-mb-44 flex-wrap items-center gap-2">
        <Bouton occupe={enCours} onClick={() => void retenir()}>
          Retenir cet outillage
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

/**
 * Un élément proposé : son sujet, son nom, sa commande quand le nom ne la dit pas, et
 * sa raison. Un `div` par élément, qui **groupe** le sujet et sa valeur dans une carte
 * étroite (deux lignes) et s'efface (`contents`) quand la grille les aligne en colonnes.
 */
function ElementPropose({ element }: { element: ElementProposeOutillage }) {
  const valeur = valeurDite(element.valeur, element.libelle);
  const manque = (element.absents?.length ?? 0) > 0;
  return (
    <div className="flex min-w-0 flex-col @md:contents">
      <dt className="text-annexe text-texte-secondaire @md:text-corps">{element.sujet}</dt>
      <dd className="flex min-w-0 flex-col">
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <span className="font-medium text-texte">{element.libelle}</span>
          {valeur !== null && (
            <code className="min-w-0 break-words font-mono text-annexe text-texte-secondaire">
              {valeur}
            </code>
          )}
        </span>
        {/* La raison dit déjà qu'un outil manque au poste (#1343) : sa ligne passe au
            ton d'attention, et ce sont ses mots — pas la couleur seule — qui le disent. */}
        {element.raison !== "" && (
          <span
            className={
              "min-w-0 break-words text-annexe " +
              (manque ? "text-attention-texte" : "text-texte-secondaire")
            }
          >
            {element.raison}
          </span>
        )}
      </dd>
    </div>
  );
}

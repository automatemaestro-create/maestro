"use client";

/**
 * La bulle du fil, **sans ce qu'elle porte** (#483, lot 2 de #481).
 *
 * Elle vivait en privé dans `FilChat` tant qu'un fil ne portait que des
 * messages. Le cadrage y arrive — le brief, les questions de clarification et
 * leurs réponses se lisent désormais dans la conversation —, et deux formes de
 * bulle auraient donné deux fils : celui des messages et celui du cadrage,
 * distincts à l'œil sur le même écran, alors que c'est la même conversation qui
 * les porte.
 *
 * Deux propriétés à ne pas défaire :
 *
 * - **le côté dit qui parle**, et rien d'autre ne le dit : l'utilisateur à
 *   droite en plein, l'agent à gauche en surface neutre. Le pied redit le nom
 *   parce qu'une bulle relue au lecteur d'écran n'a ni gauche ni droite ;
 * - **la largeur est un choix de l'appelant**, pas une propriété de l'auteur.
 *   Un message tient dans 70 % de la colonne ; un brief de sept sections
 *   éditables, non — et le rétrécir ferait de la correction un travail de
 *   contorsion, c'est-à-dire la friction qui fait approuver sans lire (docs/05
 *   §2.7.4). D'où `pleineLargeur`, demandé au cas par cas.
 *
 * ## Ce que #697 y change : la largeur de lecture, et les jetons du socle
 *
 * **Une borne en `ch` s'ajoute au pourcentage**, et c'est le critère « largeur
 * de lecture bornée ». Depuis #691 le fil occupe l'écran : 70 % d'une colonne
 * large font des lignes de 110 caractères, où l'œil perd son retour à la ligne —
 * la mesure confortable tient entre 60 et 75. Le pourcentage **reste** pour les
 * écrans étroits, où il est la contrainte qui mord ; les deux se composent par
 * `min()`, donc c'est toujours la plus serrée des deux qui décide. La borne ne
 * s'applique pas à `pleineLargeur` : un brief est un formulaire, pas de la
 * prose.
 *
 * **Les couleurs viennent des jetons** (`globals.css`, #533) et non plus de la
 * palette brute. Ce n'était pas cosmétique : la bulle de l'utilisateur portait le
 * `bg-emerald-600` + blanc à **3,65:1** que #535 a retiré des boutons, et son
 * pied `text-neutral-400` à 2,58:1. `bg-accent` / `text-sur-ton` valent 5,36:1
 * en clair et 8,00:1 en sombre, mesurés et gardés par `tests/contraste.test.ts`.
 *
 * ⚠ Le pied de la bulle de l'utilisateur s'écrit donc en `text-sur-ton` **plein**
 * et non dans une teinte affaiblie : sa discrétion vient de sa **taille**
 * (`text-micro`, le pas que le socle réserve à l'horodatage — « lisible, pas
 * lu »), jamais d'un contraste rabaissé. C'est la règle du socle appliquée ici :
 * un `text-sur-ton/70` aurait l'air plus sobre et sortirait du barème sans que
 * rien ne le dise.
 *
 * ## Ce que #876 y change : la bulle est réservée à la personne, et un tour se
 * nomme une fois
 *
 * Partis pris **2 et 3** de la veille #820 (docs/30 §5.7, décision complète en
 * commentaire de #820), mesurés sur ChatGPT, Zulip et GitHub Discussions.
 *
 * **Seule la personne a une bulle.** Hors `utilisateur` et hors `pleineLargeur`,
 * l'enveloppe perd `border border-bord bg-surface shadow-sm` : l'agent parle
 * dans le **texte de la page**, en `text-texte` sur le fond du fil. Ce n'est pas
 * une économie d'encre — c'est ce qui fait qu'un tour de réponse se lit comme un
 * paragraphe et non comme une série de cartes, et que la bulle, redevenue rare,
 * désigne **qui** parle. Les deux exceptions ne sont pas des tolérances :
 *
 * - la bulle `bg-accent` / `text-sur-ton` de la personne **ne bouge pas** — le
 *   côté dit déjà qui parle (#483), la bulle le redit par la forme, et c'est
 *   elle qui porte l'état, jamais la couleur seule ;
 * - `pleineLargeur` **garde son cadre** : ce contenu-là est un formulaire (le
 *   brief de #483, sept sections éditables), pas de la prose. Un formulaire sans
 *   bord ne se distingue plus de la page sur laquelle il est posé.
 *
 * Ce qui **ne** part pas avec le cadre : `px-3 py-2` (le texte de l'agent reste
 * aligné sur les bulles qui l'encadrent, au lieu de se coller au bord de la
 * colonne) et la borne de lecture `min(70 %, 72ch)`, que #876 laisse telle
 * quelle — c'est la **colonne** qui se resserre (`Conversation`), pas la bulle,
 * et c'est ce resserrement qui fait enfin se recouvrir les deux côtés.
 *
 * **Un tour = un auteur, nommé une fois.** Deux propriétés dont aucune bulle ne
 * peut répondre seule — elles sont des propriétés de la **suite** des messages,
 * pas de l'un d'eux —, d'où deux drapeaux que l'appelant tranche :
 *
 * - `ouvreUnTour` espace : `mt-3` s'ajoute au `gap-3` du fil pour faire les
 *   `gap-6` entre deux tours, et `first:mt-0` l'annule sur le premier message,
 *   qui n'ouvre rien ;
 * - `nomme` nomme : voir la section suivante — depuis #1225, l'auteur se nomme
 *   **en tête** du tour et non plus à son pied.
 *
 * ## Ce que #1225 y change : un tour s'OUVRE par qui parle
 *
 * Variante **A**, retenue par le regard neuf sur trois brouillons rendus sur la
 * vraie stack (choix consigné sur le ticket sous « ## Variante retenue »), contre
 * quatre références capturées en direct le 2026-09-23 — Zulip (avatar + nom +
 * heure en tête d'un groupe), Duck.ai (pastille + nom **au-dessus** de la
 * réponse), ChatGPT et Perplexity (la demande en bulle, la réponse à plat).
 *
 * Le défaut mesuré : l'identité était un **pied**, posé sous le dernier message
 * d'un tour et **aligné à droite** — c'est-à-dire du côté de la personne, sous
 * une réponse alignée à gauche. Un tour d'agent commençait donc par du texte
 * anonyme, et la seule chose qui disait qui parlait n'arrivait qu'après l'avoir
 * lu.
 *
 * Quatre propriétés portent la décision :
 *
 * - **l'en-tête est au-dessus, le pied passe en `sr-only`.** Le nom reste
 *   annoncé à **chaque** message (règle de #483 — une bulle relue au lecteur
 *   d'écran n'a ni gauche ni droite) ; l'œil, lui, le lit une fois par tour, en
 *   tête ;
 * - **le médaillon est réservé à l'interlocuteur**, et c'est une réserve du
 *   regard neuf tranchée ici : aucune des quatre références ne médaillonne
 *   l'utilisateur — sa bulle pleine dit déjà que c'est lui. Il porte une
 *   pastille d'**initiale** et jamais une image : docs/30 §6.1 interdit une
 *   identité nouvelle ;
 * - **l'en-tête porte l'état** (`etat`), à la place de l'heure : c'est le parti
 *   pris 3 de la veille, d'après la ligne « Recherche terminée 3s » de
 *   Perplexity et les annonces « Réflexion / Réponse terminée / Réponse
 *   interrompue » de ChatGPT. Ce qu'un interlocuteur **fait en ce moment** se lit
 *   donc à l'endroit exact où son nom s'écrit, et non dans une ligne d'italique
 *   posée au pied du fil ;
 * - **les actions du message sont hors de la bulle** (`actions`) : sur le fond
 *   plein de la personne, une icône de second plan n'aurait pas de contraste à
 *   elle. Elles s'alignent du côté du message.
 *
 * ⚠ Le pied **ne passe pas par `Infobulle`** : le wrapper de celle-ci porte
 * `tabIndex={0}` (c'est sa raison d'être, #536), et un pied en `sr-only` est
 * toujours dans l'ordre de tabulation — on aurait donc un arrêt de clavier par
 * message, sur du contenu qu'on ne voit pas. L'horodatage y reste un `<time>`
 * nu ; c'est l'**en-tête**, lui visible, qui porte l'infobulle de date complète.
 */

import type { ReactNode } from "react";

import { Infobulle } from "@/components/Infobulle";
import { formatDateHeure, formatHeureCourte } from "@/lib/format";

/**
 * L'**initiale** qui tient lieu de médaillon (#1225). Aucune image : docs/30
 * §6.1 refuse une identité nouvelle, et le produit n'a pas d'avatar. Le repli
 * `?` couvre un nom vide, que rien n'interdit côté API.
 */
function initialeDe(auteur: string): string {
  return (auteur.trim().charAt(0) || "?").toLocaleUpperCase("fr");
}

/**
 * L'en-tête d'un tour : **qui parle**, et **ce qu'il fait en ce moment**.
 *
 * `aria-hidden` sur la pastille : elle répète l'initiale du nom qui la suit, et
 * un lecteur d'écran l'annoncerait deux fois. Le nom, lui, est du vrai texte.
 */
export function EnTeteDeTour({
  auteur,
  utilisateur,
  horodatage,
  etat,
}: {
  auteur: string;
  utilisateur: boolean;
  horodatage?: string;
  /** Ce qu'il fait en ce moment — prend la place de l'heure (#1225). */
  etat?: ReactNode;
}) {
  const nom = utilisateur ? "vous" : auteur;
  return (
    <p
      data-entete-de-tour=""
      className={
        "mb-1 flex items-center gap-1.5 text-annexe " +
        (utilisateur ? "justify-end" : "")
      }
    >
      {/* Le médaillon est réservé à l'interlocuteur : la bulle pleine dit déjà
          que l'autre côté est la personne, et aucune des quatre références ne
          médaillonne l'utilisateur (réserve du regard neuf, #1225). */}
      {!utilisateur && (
        <span
          aria-hidden="true"
          className="grid size-5 shrink-0 place-items-center rounded-pastille border border-bord bg-surface-creuse text-micro font-medium text-texte-secondaire"
        >
          {initialeDe(auteur)}
        </span>
      )}
      <span className="min-w-0 truncate font-medium text-texte">{nom}</span>
      {etat !== undefined ? (
        <>
          <Separateur />
          {etat}
        </>
      ) : (
        horodatage !== undefined && (
          <>
            <Separateur />
            <Infobulle texte={formatDateHeure(horodatage)}>
              <time
                className="text-micro text-texte-secondaire"
                dateTime={horodatage}
              >
                {formatHeureCourte(horodatage)}
              </time>
            </Infobulle>
          </>
        )
      )}
    </p>
  );
}

/** Le point médian de l'en-tête — décoratif, jamais lu deux fois. */
function Separateur() {
  return (
    <span aria-hidden="true" className="text-texte-secondaire">
      ·
    </span>
  );
}

export function BulleFil({
  auteur,
  utilisateur = false,
  horodatage,
  pleineLargeur = false,
  ouvreUnTour = false,
  nomme = true,
  etat,
  actions,
  children,
}: {
  /** Le nom affiché en tête — remplacé par « vous » côté utilisateur. */
  auteur: string;
  utilisateur?: boolean;
  /** Horodatage ISO, quand le fil en connaît un (une saisie en cours, non). */
  horodatage?: string;
  /** Le contenu déborde la largeur d'un message : brief, formulaire, rapport. */
  pleineLargeur?: boolean;
  /**
   * Ce message **ouvre un tour** — celui qui le précède est d'un autre auteur,
   * ou un séparateur de jour les sépare (#876). L'appelant tranche : une bulle
   * ne sait pas ce qui la précède. Défaut `false`, donc un fil qui ne groupe
   * rien (le cadrage d'un run) garde son espacement d'avant.
   */
  ouvreUnTour?: boolean;
  /**
   * Ce message nomme-t-il son auteur **à l'œil** ? C'est le premier message
   * d'une suite qui le fait (#1225) ; les autres ne le portent qu'en `sr-only`.
   * Défaut `true` — un fil qui ne groupe rien (le cadrage d'un run) nomme chaque
   * message, exactement comme son pied le faisait avant ce lot.
   */
  nomme?: boolean;
  /** Ce que l'auteur fait en ce moment, dans l'en-tête, à la place de l'heure. */
  etat?: ReactNode;
  /** Les actions de ce message — hors de la bulle, du côté du message (#1225). */
  actions?: ReactNode;
  children: ReactNode;
}) {
  // La bulle, c'est-à-dire l'enveloppe posée sur la page : la personne, et le
  // contenu pleine largeur qui est un formulaire. Le reste est du texte de page.
  const enBulle = utilisateur || pleineLargeur;
  return (
    <li
      className={
        // Une **colonne** depuis #1225 : l'en-tête de tour, la bulle, puis les
        // actions du message. Les deux qui encadrent la bulle sont hors d'elle
        // — sur le fond plein de la personne, ni l'un ni l'autre n'aurait de
        // contraste à lui.
        "flex flex-col " +
        // `gap-6` entre deux tours, `gap-3` dedans : le fil porte le `gap-3`,
        // cette marge ajoute les 12 px qui manquent. `first:mt-0` parce que le
        // premier message du fil n'ouvre rien — il occupe une ligne.
        (ouvreUnTour ? "mt-3 first:mt-0" : "")
      }
    >
      {nomme && (
        <EnTeteDeTour
          auteur={auteur}
          utilisateur={utilisateur}
          horodatage={horodatage}
          etat={etat}
        />
      )}
      <div
        className={"flex " + (utilisateur ? "justify-end" : "justify-start")}
      >
      <div
        data-bulle=""
        className={
          // `shadow-sm` est celle de `Carte` (`Primitives`) et pas une ombre
          // inventée ici : c'est la seule élévation du socle, et une bulle est
          // une surface posée sur la page au même titre qu'une carte. Elle part
          // avec le cadre quand il n'y a plus de surface à élever (#876).
          "min-w-0 rounded-lg px-3 py-2 text-corps " +
          (enBulle ? "shadow-sm " : "") +
          (pleineLargeur
            ? "w-full "
            : "max-w-[85%] sm:max-w-[min(70%,72ch)] ") +
          (utilisateur
            ? "bg-accent text-sur-ton"
            : pleineLargeur
              ? "border border-bord bg-surface text-texte"
              : "text-texte")
        }
      >
        {children}
        {/* Le pied ne sert plus qu'au lecteur d'écran (#1225) : c'est
            l'en-tête qui nomme à l'œil, et le nom reste annoncé à **chaque**
            message (#483). `sr-only` **seul** : ses déclarations (position
            absolue, marge négative, écrêtage) se départageraient avec un
            `mt-1`/`text-right` laissé à côté selon l'ordre de la feuille
            Tailwind, et non selon celui de la chaîne. */}
        <p className="sr-only">
          {utilisateur ? "vous" : auteur}
          {horodatage !== undefined && (
            <>
              {" · "}
              {/* Pas d'`Infobulle` ici : voir l'avertissement de l'en-tête —
                  son wrapper est focusable, et un pied masqué deviendrait un
                  arrêt de tabulation invisible. */}
              <time dateTime={horodatage}>{formatHeureCourte(horodatage)}</time>
            </>
          )}
        </p>
      </div>
      </div>
      {actions !== undefined && (
        <div
          className={
            "mt-0.5 flex " + (utilisateur ? "justify-end" : "justify-start")
          }
        >
          {actions}
        </div>
      )}
    </li>
  );
}

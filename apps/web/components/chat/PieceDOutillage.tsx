"use client";

/**
 * **Une pièce d'outillage**, montrée avant d'être écrite — avec le geste qui en décide
 * (#1161, docs/43 §2.2).
 *
 * L'outillage d'un projet se construit dans la conversation, un fichier à la fois :
 * `AGENTS.md`, puis chaque skill. Cette carte répond, d'un coup d'œil, à la question du
 * ticket — *qu'est-ce que Maestro va écrire dans mon projet, pourquoi, et puis-je le
 * corriger simplement ?* — et ne laisse écrire qu'après.
 *
 * ## La forme vient d'une veille et d'un choix consigné
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1161 : parmi trois
 * brouillons rendus sur la vraie stack, le regard neuf a retenu **A — le diff ouvert et
 * borné**, contre un résumé au diff replié (B : le geste se lisait avant ce qu'il
 * écrit) et le fichier montré sans diff (C : deux grammaires selon le cas). Ce qu'on ne
 * défait pas sans rejouer le même geste :
 *
 * - **le diff vit dans la carte, ouvert, et le geste juste dessous** — d'après la
 *   suggestion de revue de *GitHub*. Borné à `LIGNES_OUVERTES` lignes, le reste se
 *   déplie sur place, contrôle à gauche (hors du bouton flottant du fil, #990) ; aucune
 *   zone qui défile (une boîte défilante doit être atteignable au clavier) ;
 * - **un en-tête par fichier, avec ce que le geste fera au chemin et `+N −M`** —
 *   d'après *VS Code* ; le chemin en chasse fixe garde sa casse, c'est pourquoi il
 *   n'est pas dans le titre, que `EnTeteSection` met en capitales ;
 * - **le pourquoi en une ligne, et après une correction, la phrase de la personne** —
 *   d'après *Renovate*, dont la proposition se réécrit pour correspondre ; la même
 *   carte revient, jamais une seconde empilée. La phrase a le poids du corps de texte :
 *   c'est la justification qui s'écrira dans le fichier ;
 * - **un seul geste plein, et deux issues nommées à leur portée** : « Pas cette
 *   pièce » n'écrit rien et passe à la suivante, « Remettre l'outillage à plus tard »
 *   reporte **tout** ;
 * - **ce qui n'a pas marché se dit sur la carte, et une version en échec ne s'offre
 *   pas à l'écriture** : une commande **corrigée** qui échoue retire « Écrire » (l'API
 *   le refuse aussi, 422) et déplie la liste des commandes.
 *
 * Et ce que le regard neuf a relevé sur le brouillon retenu, repris ici : le verdict
 * porte sa légende « Commandes » ; `+N −M` et « Voir … » comptent les mêmes lignes
 * (`diffDeLaPiece`) ; un fichier neuf se lit comme un texte, le « + » en gouttière et
 * sans aplat ; après une correction, la liste des commandes se déplie, et la commande
 * revérifiée s'y lit avec son verdict.
 *
 * ⚠ **Le projet visé est nommé**, dans l'`aside` qui ne transforme pas la casse :
 * le fil est transverse (#281), et c'est ce qui empêche d'écrire dans un dossier qu'on
 * n'avait pas en tête (la garde de #1104).
 *
 * Ce composant ne juge pas si la pièce attend encore : c'est une propriété de la suite
 * des messages (`pieceEnAttente`, `lib/outillage`). Il reçoit la pièce, ou rien.
 */

import { useId, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeChevronBas, IconeDossier } from "@/components/Icones";
import { LignesDiff } from "@/components/LignesDiff";
import { BadgeEtat, Bouton } from "@/components/Primitives";
import {
  enPhrase,
  ListeVerifications,
  RecapitulatifVerifications,
  TexteAvecCode,
} from "@/components/projets/VerificationsOutillage";
import { diffDeLaPiece, LIGNES_OUVERTES, SORTS_DE_PIECE } from "@/lib/outillage";
import type { DecisionPiece, PieceProposee } from "@/lib/types";

export function PieceDOutillage({
  piece,
  trancher,
  enCours = false,
}: {
  /** La pièce, telle que l'API l'a rédigée et vérifiée. */
  piece: PieceProposee;
  /** Écrit, passe ou reporte — la pièce est désignée par son empreinte. */
  trancher: (decision: DecisionPiece, empreinte: string) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les gestes se désarment. */
  enCours?: boolean;
}) {
  const id = useId();
  const [ouvert, setOuvert] = useState(false);
  const [refus, setRefus] = useState<string | null>(null);
  const diff = diffDeLaPiece(piece);
  const montrees = ouvert ? diff.entrees : diff.entrees.slice(0, LIGNES_OUVERTES);
  const repliees = diff.entrees.length - LIGNES_OUVERTES;
  const corrigee = piece.correction !== "";
  const echouee = piece.verifications.some((v) => v.etat === "echouee");

  const agir = async (decision: DecisionPiece) => {
    setRefus(null);
    try {
      await trancher(decision, piece.empreinte);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Pièce d'outillage à écrire"
      icone={IconeDossier}
      titre="Écrire cette pièce ?"
      aside={
        <span className="text-annexe text-texte-secondaire">
          Pièce {piece.rang} sur {piece.total} · {piece.projet_nom}
        </span>
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <code className="font-mono text-corps font-medium break-all text-texte">
          {piece.chemin}
        </code>
        <BadgeEtat contour>{SORTS_DE_PIECE[piece.sort] ?? piece.sort}</BadgeEtat>
        <span className="chiffre text-annexe text-texte-secondaire">
          +{diff.ajouts} −{diff.retraits}
        </span>
      </div>
      {corrigee ? (
        <p className="mt-1 text-corps text-texte">
          {/* Les espaces insécables en chaînes : une entité `&nbsp;` en fin de ligne
              laissait l'indentation suivante dans le texte servi (vu sur la vraie stack). */}
          {"Corrigée d'après votre demande : « "}
          <TexteAvecCode texte={piece.correction} />
          {" »"}
        </p>
      ) : (
        <p className="mt-1 text-annexe text-texte-secondaire">{enPhrase(piece.raison)}</p>
      )}

      <div id={`${id}-diff`} className="mt-3 rounded-controle border border-bord bg-surface">
        <LignesDiff entrees={montrees} aplatDesAjouts={!diff.neuf} />
      </div>
      {repliees > 0 && (
        <Bouton
          variante="discret"
          ton="neutre"
          taille="petite"
          icone={IconeChevronBas}
          aria-expanded={ouvert}
          aria-controls={`${id}-diff`}
          className="mt-1"
          onClick={() => setOuvert((o) => !o)}
        >
          {ouvert
            ? "Replier"
            : diff.neuf
              ? `Voir le fichier entier (${diff.ajouts} lignes)`
              : "Voir tout le changement"}
        </Bouton>
      )}

      {piece.verifications.length > 0 && (
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="text-annexe text-texte-secondaire">
              {corrigee ? "Commandes, rejouées après votre correction" : "Commandes"}
            </span>
            <RecapitulatifVerifications verifications={piece.verifications} />
          </div>
          {/* Dépliée d'elle-même quand elle a quelque chose à apprendre : un échec
              (sa sortie), ou une correction (la commande revérifiée, son verdict). */}
          {(echouee || corrigee) && (
            <div className="border-t border-bord pt-2">
              <ListeVerifications verifications={piece.verifications} />
            </div>
          )}
        </div>
      )}

      {!piece.ecrivable && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          Rien n&apos;a été écrit : <TexteAvecCode texte={piece.echec} /> Dites-moi la bonne
          commande, ou passez cette pièce.
        </p>
      )}

      <p className="mt-3 text-annexe text-texte-secondaire">
        Quelque chose ne va pas ? Dites-le juste en dessous, avec vos mots — je la
        revérifierai avant de vous la remontrer.
        {piece.regime === "branche" &&
          " Ce projet est versionné : la pièce s'écrit sur une branche, fusionnée à votre accord."}
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {piece.ecrivable && (
          <Bouton occupe={enCours} onClick={() => void agir("ecrire")}>
            Écrire ce fichier
          </Bouton>
        )}
        <Bouton
          variante="contour"
          ton="neutre"
          disabled={enCours}
          onClick={() => void agir("passer")}
        >
          Pas cette pièce
        </Bouton>
        <Bouton
          variante="discret"
          ton="neutre"
          disabled={enCours}
          onClick={() => void agir("plus-tard")}
        >
          Remettre l&apos;outillage à plus tard
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

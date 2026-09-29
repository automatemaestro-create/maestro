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
 * sans aplat.
 *
 * Puis ce que la relecture a vu sur la vraie stack :
 *
 * - **le diff est borné en hauteur aussi**, pas seulement en lignes : dans la colonne
 *   de 320 px, douze lignes repliées chacune plusieurs fois poussaient les gestes hors
 *   de la vue. Coupé, il le dit dans la boîte (un « ⋯ » à la façon de ses replis) et
 *   par le même contrôle, « Voir tout le changement » ;
 * - **la commande corrigée se lit avec son verdict juste sous la légende** (`corrigees`),
 *   sans déplier la liste — elle était sous la ligne de flottaison ; la liste entière
 *   se déplie à la demande, et d'elle-même sur un échec ;
 * - **un fichier neuf corrigé s'ouvre sur le passage corrigé** (`apercuDeLaPiece`),
 *   pas sur son début : ses douze premières lignes ne montraient pas ce que la
 *   correction allait écrire.
 *
 * Et depuis #1381, une commande que **Maestro propose** — à la revue d'après un run, celle
 * que l'outillage écrivait y avait échoué, il a lu celle que le projet construit montre
 * et l'a jouée — revient sur la **même carte**, avec la même grammaire : son verdict juste
 * sous la légende, et une phrase qui dit **d'où il la tient**, le fichier lu. Jamais
 * « d'après votre demande » : personne ne l'a dite, et c'est ce qui la distingue d'une
 * correction.
 *
 * ⚠ **Le projet visé est nommé**, dans l'`aside` qui ne transforme pas la casse :
 * le fil est transverse (#281), et c'est ce qui empêche d'écrire dans un dossier qu'on
 * n'avait pas en tête (la garde de #1104).
 *
 * Ce composant ne juge pas si la pièce attend encore : c'est une propriété de la suite
 * des messages (`pieceEnAttente`, `lib/outillage`). Il reçoit la pièce, ou rien.
 */

import { useId, useLayoutEffect, useRef, useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import {
  IconeChevronBas,
  IconeDossier,
  IconeStatutAFaire,
  IconeStatutEchec,
  IconeStatutTerminee,
} from "@/components/Icones";
import { LignesDiff } from "@/components/LignesDiff";
import { BadgeEtat, Bouton } from "@/components/Primitives";
import {
  enPhrase,
  ListeVerifications,
  RecapitulatifVerifications,
  TexteAvecCode,
} from "@/components/projets/VerificationsOutillage";
import { ErreurApi } from "@/lib/api";
import {
  apercuDeLaPiece,
  commandesMisesEnAvant,
  diffDeLaPiece,
  LIGNES_OUVERTES,
  SORTS_DE_PIECE,
} from "@/lib/outillage";
import type { DecisionPiece, PieceEcrite, PieceProposee } from "@/lib/types";

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
  const [commandesOuvertes, setCommandesOuvertes] = useState(false);
  const [coupe, setCoupe] = useState(false);
  const [refus, setRefus] = useState<string | null>(null);
  const cadre = useRef<HTMLDivElement>(null);
  const diff = diffDeLaPiece(piece);
  const montrees = ouvert ? diff.entrees : apercuDeLaPiece(piece, diff);
  const repliees = diff.entrees.length - LIGNES_OUVERTES;
  const corrigee = piece.correction !== "";
  const proposee = (piece.proposees ?? []).length > 0;
  const echouee = piece.verifications.some((v) => v.etat === "echouee");
  const enAvant = commandesMisesEnAvant(piece);
  const dites = piece.verifications.filter((v) => enAvant.includes(v.commande));
  // La première ligne montrée qui porte une commande corrigée ou proposée : la borne ne la
  // coupe pas.
  const focale = ouvert
    ? -1
    : montrees.findIndex((e) => e.type !== "repli" && enAvant.some((c) => e.texte.includes(c)));

  // Le diff replié est borné en hauteur aussi, et **coupé à une ligne entière** : la
  // seconde relecture l'avait vu tranché à mi-hauteur d'une ligne, à 320 px comme sur un
  // téléphone. La borne est plus basse quand la boîte est étroite — les lignes s'y
  // replient, et la carte entière doit tenir au-dessus du composeur. Ce qui dépasse se
  // dit par le contrôle qui déplie. Elle s'allonge jusqu'à la ligne **corrigée** quand il
  // y en a une : sur un téléphone, repliée huit fois, elle tombait sous la borne, et la
  // carte redevenait muette sur ce que la correction écrit. jsdom ne mesure rien : la
  // carte y reste bornée en lignes, et rien n'y est coupé.
  const [hauteur, setHauteur] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = cadre.current;
    if (el === null || ouvert) {
      setCoupe(false);
      setHauteur(null);
      return;
    }
    const mesurer = () => {
      const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
      const lignes = Array.from(el.firstElementChild?.children ?? []) as HTMLElement[];
      const fin = (ligne: HTMLElement) => ligne.offsetTop + ligne.offsetHeight;
      const ligneCorrigee = focale >= 0 ? lignes[focale] : undefined;
      const limite = Math.max(
        (el.clientWidth < 360 ? 12 : 18) * rem,
        ligneCorrigee !== undefined ? fin(ligneCorrigee) : 0,
      );
      if (lignes.every((ligne) => fin(ligne) <= limite)) {
        setCoupe(false);
        setHauteur(null);
        return;
      }
      const entieres = lignes.filter((ligne) => fin(ligne) <= limite);
      setCoupe(true);
      // La place du repère de coupe (`h-6`), posé là où la ligne suivante commence.
      setHauteur((entieres.length > 0 ? Math.max(...entieres.map(fin)) : limite) + 1.5 * rem);
    };
    mesurer();
    if (typeof ResizeObserver === "undefined") return;
    const observateur = new ResizeObserver(mesurer);
    observateur.observe(el);
    return () => observateur.disconnect();
  }, [ouvert, montrees.length, focale]);

  const agir = async (decision: DecisionPiece) => {
    setRefus(null);
    try {
      await trancher(decision, piece.empreinte);
    } catch (e: unknown) {
      setRefus(refusEnMots(e));
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
      {corrigee && (
        <p className="mt-1 text-corps text-texte">
          {/* Les espaces insécables en chaînes : une entité `&nbsp;` en fin de ligne
              laissait l'indentation suivante dans le texte servi (vu sur la vraie stack). */}
          {"Corrigée d'après votre demande : « "}
          <TexteAvecCode texte={piece.correction} />
          {" »"}
        </p>
      )}
      {proposee && (
        <p className="mt-1 text-corps text-texte">
          <TexteAvecCode texte={phraseDeProposition(piece.lues_dans ?? [])} />
        </p>
      )}
      {!corrigee && !proposee && (
        <p className="mt-1 text-annexe text-texte-secondaire">{enPhrase(piece.raison)}</p>
      )}

      <div
        id={`${id}-diff`}
        ref={cadre}
        className="relative mt-3 overflow-hidden rounded-controle border border-bord bg-surface"
        style={coupe && hauteur !== null ? { maxHeight: hauteur } : undefined}
      >
        <LignesDiff entrees={montrees} aplatDesAjouts={!diff.neuf} repliInchange={!diff.neuf} />
        {/* Coupée, la boîte le dit elle-même, dans la grammaire de ses replis : deux
            relectures l'avaient vue finir sur une ligne entière sans que rien dise que
            le texte continuait. Le contrôle qui déplie, juste dessous, porte le reste. */}
        {coupe && (
          <p
            aria-hidden
            className="absolute inset-x-0 bottom-0 h-6 bg-surface-creuse px-2 text-center font-mono text-annexe leading-6 text-texte-secondaire"
          >
            ⋯
          </p>
        )}
      </div>
      {(repliees > 0 || coupe || ouvert) && (
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
              {corrigee
                ? "Commandes, rejouées après votre correction"
                : proposee
                  ? "Commandes, rejouées sur le projet construit"
                  : "Commandes"}
            </span>
            <RecapitulatifVerifications verifications={piece.verifications} />
          </div>
          {/* La commande corrigée ou proposée, avec son verdict, sans rien déplier :
              c'est elle que cette version change. */}
          {dites.length > 0 && !echouee && <ListeVerifications verifications={dites} />}
          {/* La liste entière : dépliée d'elle-même sur un échec (sa sortie), à la
              demande sinon. */}
          {(echouee || commandesOuvertes) && (
            <div id={`${id}-commandes`} className="border-t border-bord pt-2">
              <ListeVerifications verifications={piece.verifications} />
            </div>
          )}
          {!echouee && piece.verifications.length > dites.length && (
            <Bouton
              variante="discret"
              ton="neutre"
              taille="petite"
              icone={IconeChevronBas}
              aria-expanded={commandesOuvertes}
              aria-controls={`${id}-commandes`}
              className="self-start"
              onClick={() => setCommandesOuvertes((o) => !o)}
            >
              {commandesOuvertes
                ? "Replier les commandes"
                : piece.verifications.length === 1
                  ? "Voir la commande"
                  : `Voir les ${piece.verifications.length} commandes`}
            </Bouton>
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

/**
 * D'où Maestro tient la commande qu'il propose (#1381), en une phrase : le fichier lu, et
 * pourquoi il l'a cherchée. Jamais « vous avez dit » — personne ne l'a dite.
 */
export function phraseDeProposition(luesDans: string[]): string {
  const ou =
    luesDans.length === 0
      ? "dans le projet construit"
      : `dans ${luesDans.map((chemin) => `\`${chemin}\``).join(" et ")}`;
  return (
    `Proposée par Maestro, lue ${ou} : la commande écrite avant le run ` +
    "échouait sur le projet construit."
  );
}

/**
 * Ce qu'un geste refusé dit sur la carte. Une API qui n'a **pas répondu** (la panne est
 * typée à la source, `ErreurApi.injoignable`) se dit avec ce qu'elle implique — rien
 * n'a été écrit — et le geste qui y répond ; un refus motivé garde son motif. Vu à la
 * relecture de #1161 : l'API coupée, la carte affichait « Failed to fetch ».
 */
function refusEnMots(e: unknown): string {
  if (e instanceof ErreurApi && e.statut === null) {
    return "L'API n'a pas répondu : rien n'a été écrit. Vérifiez que maestro-api tourne, puis réessayez.";
  }
  return e instanceof Error ? e.message : String(e);
}

/**
 * Le sort d'une pièce, en mots — la fin de sa trace sous la bulle.
 *
 * `ecrit` et `inchange` disent qu'elle est dans le projet, `ecartee` qu'on l'a passée ;
 * tout autre état dit qu'elle n'a **pas** été écrite, avec la raison que l'écriture a
 * donnée — une non-écriture qui se lirait comme une écriture est ce que le rapport de
 * génération refusait déjà (#1034).
 */
export function pieceEcriteEnMots(fait: PieceEcrite): string {
  if (fait.etat === "ecrit") return "écrit.";
  if (fait.etat === "inchange") return "déjà à jour.";
  if (fait.etat === "ecartee") return "passé : rien n'a été écrit.";
  return `pas écrit : ${fait.raison}`;
}

/**
 * **La trace d'une pièce tranchée**, sous la bulle de la réponse (#1161).
 *
 * Le parti pris 4 de la veille, d'après la carte de checkpoint de *Replit* : une fois
 * faite, la pièce quitte le pied du fil et laisse **une ligne** — un glyphe et un mot
 * qui disent son sort (écrite, passée, pas écrite), les verdicts de ses commandes, et
 * ce qui a été écrit **derrière un clic** (« Voir ce qui a été écrit »). La relecture
 * l'avait vue réduite au chemin et à un mot, la même icône de dossier pour tous les
 * sorts.
 *
 * `piece` est la version que la carte montrait — relue du fil par son empreinte
 * (`piecesDuFil`) : le fait ne recopie ni le diff ni les verdicts, ils sont déjà
 * persistés sur le message qui la proposait. Sans elle (un fil tronqué), la ligne se
 * réduit au sort.
 */
export function TraceDePiece({
  fait,
  piece,
}: {
  fait: PieceEcrite;
  piece?: PieceProposee;
}) {
  const id = useId();
  const [ouvert, setOuvert] = useState(false);
  const dansLeProjet = fait.etat === "ecrit" || fait.etat === "inchange";
  const IconeDuSort = dansLeProjet
    ? IconeStatutTerminee
    : fait.etat === "ecartee"
      ? IconeStatutAFaire
      : IconeStatutEchec;
  const diff = piece !== undefined && fait.etat === "ecrit" ? diffDeLaPiece(piece) : null;
  return (
    <div className="flex flex-col gap-1">
      {/* Le fait au pas des badges qui le suivent, le chemin en tête : la sixième
          relecture lisait « ✓ 3 vérifiées » avant de savoir quel fichier était écrit. */}
      <p
        className={
          "flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe " +
          (dansLeProjet
            ? "text-texte"
            : fait.etat === "ecartee"
              ? "text-texte-secondaire"
              : "text-attention-texte")
        }
      >
        <span className="inline-flex min-w-0 items-center gap-1">
          <IconeDuSort className="size-3.5 shrink-0" />
          <span className="min-w-0 break-words">
            <span className="font-mono font-medium">{fait.chemin}</span>{" "}
            {pieceEcriteEnMots(fait)}
          </span>
        </span>
        {piece !== undefined && dansLeProjet && piece.verifications.length > 0 && (
          <RecapitulatifVerifications verifications={piece.verifications} />
        )}
        {diff !== null && (
          <Bouton
            variante="discret"
            ton="neutre"
            taille="petite"
            icone={IconeChevronBas}
            aria-expanded={ouvert}
            aria-controls={`${id}-ecrit`}
            onClick={() => setOuvert((o) => !o)}
          >
            {ouvert ? "Replier" : "Voir ce qui a été écrit"}
          </Bouton>
        )}
      </p>
      {diff !== null && ouvert && (
        <div id={`${id}-ecrit`} className="rounded-controle border border-bord bg-surface">
          <LignesDiff entrees={diff.entrees} aplatDesAjouts={!diff.neuf} />
        </div>
      )}
    </div>
  );
}

"use client";

/**
 * La demande de cadrage du fil, **avec le geste qui y répond** (#943).
 *
 * L'orchestration propose un run et demande l'accord — « Je lance ? ». Jusqu'à
 * ce lot, cette question n'existait que dans le texte de sa réponse : on y
 * répondait au clavier, ou on ne savait pas qu'on pouvait y répondre (retex du
 * 2026-09-11, constat G10). Elle porte désormais son geste.
 *
 * ## Rien d'inventé ici : c'est la carte « Décision » du brief
 *
 * Le produit a déjà tranché à quoi ressemble un cadrage qui attend quelqu'un
 * (#322, #483) : une carte `ton="attention"` au pied du fil, à la place de la
 * zone de saisie, ce qui part **éditable au-dessus**, et deux boutons —
 * approuver, refuser. `components/chat/CadrageDansLeFil` la rend pour le brief
 * d'un run arrêté ; celle-ci la rend pour la proposition qui précède le run. Un
 * seul écran, deux moments, une seule forme : la question « est-ce que je
 * lance ? » ne doit pas se répondre de deux façons selon l'instant.
 *
 * Les **trois** gestes du critère tiennent donc dans cette forme sans en ajouter
 * une quatrième : accepter et refuser sont les deux boutons, **amender** est la
 * correction sur place — comme un brief se corrige avant d'être approuvé, et
 * avec le même badge pour dire que c'est la version corrigée qui partira.
 *
 * ## Deux propriétés du contrat, portées ici comme sur le brief
 *
 * - un objectif **touché** part corrigé, un objectif **intact** part en `null` :
 *   le corps ne recopie jamais ce qu'on n'a pas changé (§6.10, et §6.15 pour
 *   cette route-ci) ;
 * - un **refus n'emporte rien** : il n'ouvre pas de run, n'en annule aucun — il
 *   n'y en a pas encore — et laisse la conversation continuer.
 *
 * ⚠ Ce composant ne juge pas si la demande attend encore : c'est une propriété
 * de la **suite** des messages, pas de l'un d'eux, et elle s'énonce une seule
 * fois (`propositionEnAttente`, `lib/brief`). Il reçoit la demande ou rien.
 *
 * ## Et c'est ici qu'on borne le run (#990)
 *
 * Le moteur sait arrêter un run sur quatre garde-fous, mais la conversation —
 * seule porte de lancement depuis #666 — ne les passait pas : un run lancé d'un
 * écran était sans borne, et le retex du 2026-09-11 en a mesuré un à 12,51 $.
 * Ils se posent donc **là où l'on décide**, dans cette carte, entre l'objectif
 * et les deux boutons.
 *
 * La forme vient d'une veille de conception et d'un choix rendu sur pièces
 * (commentaires « Veille de conception » et « Variante retenue » de #990) —
 * trois variantes rendues sur la vraie stack, jugées par un regard qui n'en
 * était pas l'auteur. Ce qu'elle tranche, et qu'on ne défait pas sans rejouer
 * le même geste :
 *
 * - **au lancement, pas dans un écran de réglages** — d'après le playground de
 *   Replicate, où l'objectif et ses bornes tiennent dans un seul formulaire et
 *   où le bouton « Run » est à son pied. La variante qui envoyait le réglage
 *   dans Paramètres a été écartée : elle obligeait à quitter une proposition en
 *   attente pour aller régler un défaut ailleurs ;
 * - **repliées, et le repli porte le récapitulatif** — d'après Vercel Spend
 *   Management, dont la ligne fermée porte le chiffre et l'état plutôt qu'un
 *   mot générique. La variante qui laissait les quatre champs ouverts en
 *   permanence a été écartée : elle alourdit **chaque** proposition ;
 * - **le contrôle est à gauche de la ligne**, et c'est une correction du regard
 *   neuf : au bord droit, il tombait exactement sous le bouton flottant
 *   « ↓ Dernier message » du fil, qui recouvrait l'affordance d'ouverture ;
 * - **le régime se lit sans rien ouvrir**, y compris quand il n'y a aucune
 *   borne (`phraseDesBornes`, `lib/bornes`) — troisième critère du ticket :
 *   l'illimité est un choix affiché, pas un oubli.
 *
 * Les champs n'ont **pas** de `placeholder` gris disant « aucun » : le regard
 * neuf a relevé qu'à l'œil c'est un champ vide, pas une valeur. Ce que vaut
 * l'absence est écrit dans l'aide du champ, en toutes lettres.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeChevronBas, IconeObjectif } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  Champ,
  ChampTexte,
} from "@/components/Primitives";
import {
  SAISIE_VIERGE,
  bornesDepuis,
  champFautif,
  phraseDeLaSaisie,
  saisieFautive,
  type BornesRun,
  type SaisieBornes,
} from "@/lib/bornes";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import type { MessageChat } from "@/lib/types";

/**
 * Ce qu'un champ de borne dit quand il est rempli sans porter de borne — une
 * lettre, un zéro, un nombre négatif.
 *
 * L'écran **nomme** la faute, il n'en est pas l'autorité : la règle « un
 * plafond est un maximum » vit dans `ServiceExecutions.lancer`, qui refuse
 * toujours, et son refus se raconte dans le fil. Le dire ici évite seulement
 * qu'un « abc » saisi en plafond de coût parte en `null`, c'est-à-dire en run
 * sans borne — l'exact défaut que ce ticket corrige.
 */
const FAUTE = "Un nombre supérieur à zéro, ou rien.";

export function DemandeDeCadrage({
  demande,
  trancher,
  enCours = false,
}: {
  /** Le message qui porte la proposition — `demande.proposition` est l'objectif. */
  demande: MessageChat;
  /**
   * `objectif` vaut `null` quand rien n'a été touché (voir l'en-tête) ;
   * `bornes` porte jusqu'où le run pourra aller (#990), `null` sur un refus —
   * il n'y a alors rien à borner.
   */
  trancher: (
    approuve: boolean,
    objectif: string | null,
    bornes: BornesRun | null,
  ) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les deux gestes se désarment. */
  enCours?: boolean;
}) {
  const maintenant = useHorloge();
  const propose = demande.proposition ?? "";
  const [edite, setEdite] = useState(propose);
  const [refus, setRefus] = useState<string | null>(null);
  const [saisie, setSaisie] = useState<SaisieBornes>(SAISIE_VIERGE);
  const [bornesOuvertes, setBornesOuvertes] = useState(false);

  const regime = phraseDeLaSaisie(saisie);
  const bornesFautives = saisieFautive(saisie);
  const changerBorne = (cle: keyof SaisieBornes) => (valeur: string) =>
    setSaisie((avant) => ({ ...avant, [cle]: valeur }));

  // Comparé sur la version **normalisée** (celle qui partirait) et non sur la
  // frappe : ajouter une espace puis la retirer n'est pas une correction. Même
  // règle qu'`estCorrige` pour le brief (`lib/brief`), sur un seul champ.
  const retenu = edite.trim();
  const vide = retenu === "";
  // Un champ **vidé** n'est pas une correction : il n'y a rien à lancer, et
  // annoncer « la version corrigée » sur un bouton désarmé nommerait une
  // version qui n'existe pas. Le manque se dit une fois, en dessous.
  const corrige = !vide && retenu !== propose.trim();

  const surDecision = async (approuve: boolean) => {
    setRefus(null);
    try {
      // Un refus n'emporte rien : ni l'objectif corrigé, ni les bornes. Il n'y
      // a pas encore de run, donc rien à borner.
      await trancher(
        approuve,
        approuve && corrige ? retenu : null,
        approuve ? bornesDepuis(saisie) : null,
      );
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Décision sur le cadrage"
      icone={IconeObjectif}
      titre="Lancer ce run ?"
      aside={
        corrige ? (
          <BadgeEtat ton="info">
            Corrigé — c&apos;est cette version qui partira
          </BadgeEtat>
        ) : demande.horodatage ? (
          <span className="text-annexe text-texte-secondaire">
            proposé {formatHeureRelative(demande.horodatage, maintenant)}
          </span>
        ) : undefined
      }
    >
      <p className="mb-3 text-annexe text-texte-secondaire">
        Relisez, corrigez si besoin : <strong>c&apos;est cet objectif</strong>{" "}
        qui sera cadré puis décomposé en tâches. Rien ne part avant votre
        accord.
      </p>
      <ChampTexte
        id="cadrage-objectif"
        libelle="Objectif proposé"
        value={edite}
        onChange={(e) => setEdite(e.target.value)}
        disabled={enCours}
        rows={3}
      />
      {/* Les bornes du run (#990). Le contrôle est **à gauche** : au bord droit,
          il passe sous le bouton flottant « ↓ Dernier message » du fil.
          `attentionClaire` est le ton prévu pour une carte posée *dans* une zone
          `attention` — elle garde le fond du contenu et n'emprunte que le bord ;
          et c'est une `Carte`, pas un `div` habillé, parce que le pas de padding
          se choisit une fois (barème de `apps/web/README.md`). */}
      <Carte
        balise="div"
        ton="attentionClaire"
        densite="compacte"
        className="mt-3"
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <Bouton
            variante="discret"
            ton="neutre"
            taille="petite"
            // `IconeChevronBas` dit « ce contrôle s'ouvre » (#280), et c'est la
            // seule du jeu qui le dise : `IconeDeplier`/`IconeReplier` sont les
            // chevrons **horizontaux** de la barre latérale, qui pointeraient
            // de côté sur un repli qui s'ouvre vers le bas (constat de la
            // relecture visuelle). L'état, lui, est porté par `aria-expanded`
            // et par les champs qui apparaissent — jamais par l'icône seule.
            icone={IconeChevronBas}
            aria-expanded={bornesOuvertes}
            aria-controls="cadrage-bornes"
            onClick={() => setBornesOuvertes((ouvertes) => !ouvertes)}
          >
            Bornes du run
          </Bouton>
          {/* Le récapitulatif se lit **sans rien ouvrir**, et il parle aussi
              quand il n'y a aucune borne : c'est le critère 3 du ticket. */}
          <span className="min-w-0 flex-1 text-annexe text-texte-secondaire">
            {regime}
          </span>
        </div>
        {bornesOuvertes && (
          <div id="cadrage-bornes" className="mt-3 grid gap-3 @md:grid-cols-2">
            <Champ
              id="cadrage-borne-cout"
              libelle="Coût maximal"
              aide="En dollars. Le run s'interrompt quand il l'atteint. Vide : aucune borne."
              erreur={champFautif(saisie.cout) ? FAUTE : undefined}
              inputMode="decimal"
              value={saisie.cout}
              onChange={(e) => changerBorne("cout")(e.target.value)}
              disabled={enCours}
            />
            <Champ
              id="cadrage-borne-tokens"
              libelle="Tokens maximum"
              aide="Le run s'interrompt quand il les a consommés. Vide : aucune borne."
              erreur={champFautif(saisie.tokens) ? FAUTE : undefined}
              inputMode="numeric"
              value={saisie.tokens}
              onChange={(e) => changerBorne("tokens")(e.target.value)}
              disabled={enCours}
            />
            <Champ
              id="cadrage-borne-delai"
              libelle="Délai par tâche"
              aide="En secondes. Au-delà, la tâche est arrêtée. Vide : aucune borne."
              erreur={champFautif(saisie.delai) ? FAUTE : undefined}
              inputMode="numeric"
              value={saisie.delai}
              onChange={(e) => changerBorne("delai")(e.target.value)}
              disabled={enCours}
            />
            <Champ
              id="cadrage-borne-parallelisme"
              libelle="Tâches en parallèle"
              aide="Combien d'agents travaillent en même temps. Vide : au choix du moteur."
              erreur={champFautif(saisie.parallelisme) ? FAUTE : undefined}
              inputMode="numeric"
              value={saisie.parallelisme}
              onChange={(e) => changerBorne("parallelisme")(e.target.value)}
              disabled={enCours}
            />
          </div>
        )}
      </Carte>
      <div className="mt-4 flex flex-wrap gap-2">
        <Bouton
          disabled={vide || bornesFautives}
          occupe={enCours}
          onClick={() => void surDecision(true)}
        >
          {corrige ? "Lancer la version corrigée" : "Lancer"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="alerte"
          disabled={enCours}
          onClick={() => void surDecision(false)}
        >
          Ne pas lancer
        </Bouton>
      </div>
      {vide && (
        <p className="mt-2 text-annexe text-attention-texte">
          Sans objectif, il n&apos;y a rien à lancer — restaurez le texte ou
          refusez.
        </p>
      )}
      {/* Une borne illisible partirait en « aucune borne », c'est-à-dire en run
          sans limite : le défaut même que ce ticket corrige. On le dit et on
          désarme, plutôt que de lancer autre chose que ce qui est écrit. */}
      {bornesFautives && (
        <p className="mt-2 text-annexe text-attention-texte">
          Une borne est saisie mais illisible — corrigez-la, ou videz le champ
          pour lancer sans elle.
        </p>
      )}
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}

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
 */

import { useState } from "react";

import { IconeObjectif } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  ChampTexte,
  EnTeteSection,
} from "@/components/Primitives";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import type { MessageChat } from "@/lib/types";

export function DemandeDeCadrage({
  demande,
  trancher,
  enCours = false,
}: {
  /** Le message qui porte la proposition — `demande.proposition` est l'objectif. */
  demande: MessageChat;
  /** `objectif` vaut `null` quand rien n'a été touché (voir l'en-tête). */
  trancher: (approuve: boolean, objectif: string | null) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les deux gestes se désarment. */
  enCours?: boolean;
}) {
  const maintenant = useHorloge();
  const propose = demande.proposition ?? "";
  const [edite, setEdite] = useState(propose);
  const [refus, setRefus] = useState<string | null>(null);

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
      await trancher(approuve, approuve && corrige ? retenu : null);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Carte
      balise="section"
      ton="attention"
      densite="aeree"
      aria-label="Décision sur le cadrage"
    >
      <EnTeteSection
        niveau={3}
        icone={IconeObjectif}
        titre="Lancer ce run ?"
        ton="attention"
        className="mb-3"
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
      />
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
      <div className="mt-4 flex flex-wrap gap-2">
        <Bouton
          disabled={vide}
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
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </Carte>
  );
}

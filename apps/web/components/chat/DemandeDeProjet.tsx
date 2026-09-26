"use client";

/**
 * Le projet que l'orchestration propose de déclarer, **avec le geste qui y
 * répond** (#1294, docs/43 §2.2).
 *
 * « Nouveau projet » ouvre la conversation ; l'orchestration comprend ce qu'on
 * veut faire, puis propose un nom, un dossier et le versionnement, chacun avec sa
 * raison. Cette carte les montre **tels que l'API les a vérifiés** — et ce que la
 * vérification a changé (un nom pris, un dossier occupé, Git absent) est dit
 * dessous, en toutes lettres : c'est un fait du code, pas une parole du modèle.
 *
 * ## Ce que la veille et le choix de variante ont tranché
 *
 * La forme vient de la veille de conception de #1294 et de la variante retenue
 * sur pièces par le regard neuf (commentaires « Veille de conception » et
 * « Variante retenue » du ticket). Ce qu'on ne défait pas sans rejouer le geste :
 *
 * - **une carte du pied du fil**, comme la demande de cadrage (#943) et d'après la
 *   carte d'approbation de Replit : un titre, la proposition, **un** geste
 *   principal ;
 * - **aucun champ éditable.** Corriger se dit dans la conversation (« appelle-le
 *   racines »), et appelle une proposition nouvelle, revérifiée. Des champs
 *   remettraient dans une carte le formulaire à étapes que ce ticket retire du
 *   chemin de création — la carte le rappelle en une ligne ;
 * - **chaque choix avec sa raison**, écrite pour ce projet-là : on accepte en
 *   connaissance de cause, pas un formulaire prérempli.
 *
 * ⚠ Ce composant ne juge pas si la proposition attend encore : c'est une
 * propriété de la suite des messages (`projetEnAttente`, `lib/naissance`). Il
 * reçoit la proposition ou rien.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeProjets } from "@/components/Icones";
import { Bouton } from "@/components/Primitives";
import { versionnementEnMots } from "@/lib/naissance";
import type { DemandeProjet } from "@/lib/types";

/** Une ligne de la proposition : le choix, puis ce qui le justifie. */
function Choix({
  terme,
  raison,
  children,
}: {
  terme: string;
  raison: string;
  children: React.ReactNode;
}) {
  return (
    <>
      <dt className="text-texte-secondaire">{terme}</dt>
      <dd className="flex min-w-0 flex-col">
        {children}
        {raison !== "" && (
          <span className="text-annexe text-texte-secondaire">{raison}</span>
        )}
      </dd>
    </>
  );
}

export function DemandeDeProjet({
  demande,
  declarer,
  enCours = false,
}: {
  /** La proposition, telle que l'API l'a vérifiée. */
  demande: DemandeProjet;
  /** Accepte (`true`) ou refuse (`false`) : rien d'autre ne part. */
  declarer: (approuve: boolean) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les deux gestes se désarment. */
  enCours?: boolean;
}) {
  const [refus, setRefus] = useState<string | null>(null);
  const importe = demande.origine === "existant";

  const surDecision = async (approuve: boolean) => {
    setRefus(null);
    try {
      await declarer(approuve);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Proposition de projet"
      icone={IconeProjets}
      titre={importe ? "Importer ce projet ?" : "Créer ce projet ?"}
    >
      {/* Ce que la vérification a changé, **avant** les lignes : placé dessous,
          il arrivait après « banc 2 » et sa raison « le nom que vous avez
          demandé », loin du nom qu'il corrige (relecture de clôture). */}
      {demande.ajustements.length > 0 && (
        <ul
          aria-label="Ce que la vérification a changé"
          className="mb-3 flex flex-col gap-1 text-annexe text-attention-texte"
        >
          {demande.ajustements.map((ajustement) => (
            <li key={ajustement}>{ajustement}</li>
          ))}
        </ul>
      )}
      <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-2 text-corps">
        <Choix terme="Nom" raison={demande.raison_nom}>
          <span className="font-medium text-texte">{demande.nom}</span>
        </Choix>
        <Choix terme="Dossier" raison={demande.raison_dossier}>
          <code className="font-mono text-annexe break-all text-texte">
            {demande.racine}
          </code>
          {/* Le seul fait que la raison du modèle ne dit pas toujours, et que la
              personne doit savoir avant d'accepter : ce que l'import écrira dans
              son dossier. Rien — ou, si la mise sous Git est proposée, son `.git`
              et un premier commit : la vraie stack a montré « rien n'y sera
              écrit » au-dessus d'une mise sous Git. Un dossier neuf, lui, n'a rien
              à ajouter à sa raison (la ligne doublait celle du modèle). */}
          {importe && (
            <span className="text-annexe text-texte-secondaire">
              {demande.versionner
                ? "Un dossier que vous avez déjà : l'import n'y écrit que sa mise sous Git (un dossier .git et un premier commit)."
                : "Un dossier que vous avez déjà : rien n'y sera écrit à l'import."}
            </span>
          )}
        </Choix>
        {/* Déjà sous Git : la raison est un fait du code, pas celle du modèle —
            qui a pu proposer une mise sous Git que la vérification a retirée.
            Sans raison, la ligne était la seule de la carte à n'en pas porter
            (relecture de clôture). */}
        <Choix
          terme="Versionnement"
          raison={
            demande.deja_versionne
              ? "Son dépôt Git est constaté tel quel : l'import n'y crée rien."
              : demande.raison_versionnement
          }
        >
          <span className="font-medium text-texte">
            {versionnementEnMots(demande)}
          </span>
        </Choix>
      </dl>
      <p className="mt-3 text-annexe text-texte-secondaire">
        Quelque chose ne va pas ? Dites-le juste en dessous, avec vos mots — je
        vous proposerai la version corrigée.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Bouton occupe={enCours} onClick={() => void surDecision(true)}>
          {importe ? "Importer le projet" : "Créer le projet"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="neutre"
          disabled={enCours}
          onClick={() => void surDecision(false)}
        >
          Pas maintenant
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

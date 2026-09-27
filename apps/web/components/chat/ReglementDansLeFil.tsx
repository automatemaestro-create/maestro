"use client";

/**
 * **Régler une attente depuis le fil** (#1183) — la carte qui le propose, la ligne qui
 * dit ce qui en est sorti, et les attentes entre lesquelles une demande ambiguë laisse
 * choisir.
 *
 * « Réponds-lui : prends Postgres », « oui, valide », « refuse et archive plutôt » :
 * l'orchestrateur règle ce qui attend quelqu'un par le service des écrans des questions
 * et des validations, et chaque règlement se **confirme**. Trois pièces, et c'est la
 * même anatomie que les gestes sur un run (`GesteSurUnRun`, #1179), appliquée — le
 * ticket réutilise sa carte proposée puis confirmée :
 *
 * - `ReglementDansLeFil` — la carte, au pied du fil (`useGestesDuFil`). Elle pose la
 *   **question** au verbe du règlement, nomme **ce qui attend** (`CibleDeLAttente` :
 *   la question de l'agent, ou l'acte que la validation retient, puis qui le porte),
 *   montre **ce qui partira** — la réponse que l'agent lira, la raison d'un refus —,
 *   dit **ce qui va se passer** avec les mots du service, puis porte un seul geste
 *   principal et « Pas maintenant ». Aucun champ : « dis-lui plutôt MySQL » se dit dans
 *   le composeur et appelle une carte nouvelle ;
 * - `TraceDuReglement` — sous la réponse d'après la confirmation, une ligne **cochée**
 *   qui dit ce qui a été fait et ce qui a repris — « Réponse transmise · l'agent la lit
 *   et reprend sa tâche » —, ou le **refus** du service et sa raison ;
 * - `AttentesCandidates` — sous une question du modèle, les attentes qu'une demande
 *   pouvait viser, chacune dans son encadré, **sans bouton** : on répond en mots.
 *
 * ⚠ Ce composant ne juge pas si la carte attend encore : c'est une propriété de la
 * suite des messages (`reglementEnAttente`, `lib/reglements`). Il reçoit la carte ou
 * rien.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeAide, IconeArret, IconeCoche, IconeValidations } from "@/components/Icones";
import { Bouton, Carte, type Icone } from "@/components/Primitives";
import { enPhrase, libellesDuReglement, porteurDeLAttente } from "@/lib/reglements";
import type { AttenteVisee, ReglementFait, ReglementPropose } from "@/lib/types";

/**
 * Le glyphe de chaque règlement : celui de la question d'un agent (`QuestionDansLeFil`)
 * pour lui répondre, celui des validations pour approuver, celui de l'arrêt pour
 * refuser — le glyphe du seul geste qui arrête quelque chose, comme « Interrompre ».
 */
const GLYPHES: Record<string, Icone> = {
  reponse: IconeAide,
  approbation: IconeValidations,
  refus: IconeArret,
};

/**
 * **Ce qui attend**, à ses faits : ce qu'on tranche — la question de l'agent, ou l'acte
 * de la validation dans l'ordre de son écran (`CarteValidation` : « Appel de » et
 * l'outil, jamais le titre de la tâche au-dessus d'un `rm -rf`) —, puis qui le porte.
 * La cible d'une carte, et chacune des candidates d'une demande ambiguë.
 */
export function CibleDeLAttente({ attente }: { attente: AttenteVisee }) {
  const porteur = porteurDeLAttente(attente);
  return (
    <div className="min-w-0">
      {attente.genre === "validation" && attente.outil !== "" ? (
        <p className="break-words text-corps">
          <span className="text-texte-secondaire">Appel de </span>
          <span className="font-mono text-texte">{attente.objet}</span>
        </p>
      ) : (
        /* `whitespace-pre-wrap` : une question vient d'un modèle, ses retours à la
           ligne sont les siens (la règle de `QuestionDansLeFil`). */
        <p className="break-words whitespace-pre-wrap text-corps font-medium text-texte">
          {attente.objet || attente.titre || attente.identifiant}
        </p>
      )}
      {porteur !== "" && (
        <p className="mt-1 text-annexe text-texte-secondaire">{porteur}</p>
      )}
    </div>
  );
}

export function ReglementDansLeFil({
  demande,
  trancher,
  enCours = false,
}: {
  /** Le règlement proposé, tel que le fil le porte. */
  demande: ReglementPropose;
  /** Confirme (`true`) ou écarte (`false`) : rien d'autre ne part. */
  trancher: (approuve: boolean) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les deux gestes se désarment. */
  enCours?: boolean;
}) {
  const [refus, setRefus] = useState<string | null>(null);
  const libelles = libellesDuReglement(demande.action);
  if (libelles === null) return null;

  const surDecision = async (approuve: boolean) => {
    setRefus(null);
    try {
      await trancher(approuve);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <CarteDuFil
      libelle="Règlement d'une attente"
      icone={GLYPHES[demande.action]}
      titre={libelles.question}
    >
      <CibleDeLAttente attente={demande.attente} />
      {/* Ce qui partira, tel quel : la réponse que l'agent lira — citée, comme une
          citation du fil (`TexteMarkdown`) —, ou la raison d'un refus. C'est ce que la
          personne confirme ; le modèle l'a compris de ses mots, la carte le montre. */}
      {demande.action === "reponse" && (
        <blockquote
          aria-label="Réponse transmise à l'agent"
          className="mt-3 border-s-2 border-bord ps-3 text-corps whitespace-pre-wrap text-texte"
        >
          {demande.texte}
        </blockquote>
      )}
      {demande.action === "refus" && (
        <p className="mt-3 text-corps text-texte">
          {demande.texte !== "" ? (
            <>
              Raison transmise : <strong className="font-medium">{demande.texte}</strong>
            </>
          ) : (
            "Sans raison donnée."
          )}
        </p>
      )}
      {demande.suite !== "" && (
        <p className="mt-2 text-corps text-texte-secondaire">{enPhrase(demande.suite)}</p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Bouton
          ton={demande.action === "refus" ? "alerte" : "accent"}
          occupe={enCours}
          onClick={() => void surDecision(true)}
        >
          {libelles.verbe}
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

/**
 * Ce qu'un règlement confirmé a **donné**, sous la réponse : une ligne cochée — ce qui a
 * été fait, ce qui a repris —, ou le refus du service avec sa raison et le glyphe
 * d'arrêt.
 *
 * La coche d'un **refus** est à la couleur du texte, pas au vert : la règle de
 * l'interruption (`TraceDuGeste`, #1179) — la coche dit que le geste a eu lieu, le ton
 * n'en fait pas une bonne nouvelle.
 */
export function TraceDuReglement({ fait }: { fait: ReglementFait }) {
  const libelles = libellesDuReglement(fait.action);
  if (fait.refus !== "") {
    return (
      <p className="flex items-start gap-1 text-annexe text-alerte-texte">
        <IconeArret className="mt-0.5 size-3.5 shrink-0" />
        <span className="min-w-0 break-words">
          {libelles ? `${libelles.verbe} : refusé` : "Refusé"} — {fait.refus}
        </span>
      </p>
    );
  }
  const ton = fait.action === "refus" ? "text-texte" : "text-positif-texte";
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe">
      <span className={`inline-flex items-center gap-1 font-medium ${ton}`}>
        <IconeCoche className="size-3.5 shrink-0" />
        {libelles?.fait ?? "Fait"}
      </span>
      {fait.suite !== "" && (
        <span className="min-w-0 break-words text-texte-secondaire">· {fait.suite}</span>
      )}
    </p>
  );
}

/**
 * Les attentes qu'une demande pouvait viser, quand elle en visait plusieurs — chacune à
 * ses faits, dans la carte compacte du socle, sans bouton : la question est dans les
 * mots du modèle, juste au-dessus, et la réponse se dit dans le composeur.
 */
export function AttentesCandidates({ attentes }: { attentes: AttenteVisee[] }) {
  if (attentes.length === 0) return null;
  return (
    <ul aria-label="Attentes possibles" className="flex flex-col gap-2 text-annexe">
      {attentes.map((attente) => (
        <Carte key={`${attente.genre}:${attente.identifiant}`} balise="li" densite="compacte">
          <CibleDeLAttente attente={attente} />
        </Carte>
      ))}
    </ul>
  );
}

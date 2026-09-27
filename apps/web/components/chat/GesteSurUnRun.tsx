"use client";

/**
 * **Un geste sur un run, dans le fil** (#1179) — la carte qui le propose, la ligne
 * qui dit ce qu'il a donné, et les runs entre lesquels une demande ambiguë laisse
 * choisir.
 *
 * « Mets-le en pause », « annule », « relance-le avec 5 $ de plus » : l'orchestrateur
 * agit sur les runs par le service des boutons des écrans, et chaque geste qui a un
 * effet se **confirme**. Trois pièces, qui partagent une ligne :
 *
 * - `GesteSurUnRun` — la carte, au pied du fil (`useGestesDuFil`). Elle pose la
 *   **question** au verbe du geste, nomme le run (`CibleDuRun`), dit **ce qui va se
 *   passer**, puis porte un seul geste principal et « Pas maintenant » ;
 * - `TraceDuGeste` — sous la réponse d'après la confirmation (`Suite`), une ligne
 *   **cochée** qui dit ce qui a été fait et l'état **relu** du run — « Mis en pause ·
 *   en pause depuis 14:02 » —, ou le **refus** du service et sa raison ;
 * - `RunsCandidats` — sous une question du modèle, les runs qu'une demande pouvait
 *   viser, chacun sur sa ligne `CibleDuRun`, **sans bouton** : on répond en mots
 *   (« le premier »), et la carte suit.
 *
 * ## Ce que la veille et le choix de variante ont tranché
 *
 * La forme vient de la veille de conception de #1179 et de la variante A, retenue
 * sur pièces par le regard neuf contre une carte compacte (la cible dans le titre)
 * et une transition « avant → après » (commentaires « Veille de conception » et
 * « Variante retenue » du ticket). Ce qu'on ne défait pas sans rejouer le geste :
 *
 * - **la question au verbe du geste, et ce qui va se passer en texte courant** —
 *   d'après l'approbation d'outil de VS Code (« Install Extension X? » puis ce que
 *   Copilot va faire). La phrase de conséquence est celle des écrans des runs
 *   (`lib/gestesRun`), à la couleur du texte : c'est elle qui répond à « que va-t-il
 *   se passer ? » ;
 * - **le run se reconnaît à ses faits** — d'après le retour arrière de Replit (la
 *   cible avec son état, son titre, son identifiant). Le badge est celui des écrans
 *   des runs (`BadgeRun`), jamais un second, et la ligne mène à la vue du run ;
 * - **un geste principal au verbe du geste** — « Interrompre » en ton d'alerte, seul
 *   geste sans retour, comme le bouton des écrans ; aucun champ : « plutôt 10 $ » se
 *   dit dans le composeur et appelle une carte nouvelle ;
 * - **une ligne cochée une fois fait** — d'après la trace d'une commande dans VS
 *   Code : on sait que c'est fait sans relire la phrase du modèle.
 *
 * ⚠ Ce composant ne juge pas si la carte attend encore : c'est une propriété de la
 * suite des messages (`gesteRunEnAttente`, `lib/gestesRun`). Il reçoit la carte ou
 * rien.
 */

import { useState } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import {
  IconeArret,
  IconeCoche,
  IconeHistorique,
  IconePause,
  IconeReprise,
} from "@/components/Icones";
import { Bouton, Carte, LienRenvoi, type Icone } from "@/components/Primitives";
import { BadgeRun } from "@/components/runs/EtatRun";
import { AUCUNE_BORNE, phraseDesBornes } from "@/lib/bornes";
import { causeDAttente, regimeDuRun } from "@/lib/execution";
import { formatHeureCourte, libelleStatutExecution } from "@/lib/format";
import { consequenceDuGeste, libellesDuGeste, resumeDuRunVise } from "@/lib/gestesRun";
import { hrefRun } from "@/lib/navigation";
import type { GesteRunFait, GesteRunPropose, RunVise } from "@/lib/types";

/** Le glyphe de chaque geste — ceux des boutons des écrans des runs. */
const GLYPHES: Record<string, Icone> = {
  pause: IconePause,
  reprise: IconeReprise,
  annulation: IconeArret,
  relance: IconeHistorique,
};

/**
 * Un run **à ses faits**, sur une ligne : le badge des écrans des runs, son titre,
 * son identifiant, et le renvoi vers sa vue. La cible d'une carte, et chacun des
 * candidats d'une demande ambiguë.
 */
export function CibleDuRun({ run, className = "" }: { run: RunVise; className?: string }) {
  const resume = resumeDuRunVise(run);
  const href = hrefRun(run.run_id);
  return (
    <p className={`flex flex-wrap items-center gap-x-2 gap-y-1 text-corps ${className}`}>
      <BadgeRun
        run={resume}
        regime={regimeDuRun(resume)}
        attente={causeDAttente(resume, false)}
      />
      <span className="min-w-0 break-words font-medium text-texte">
        {run.titre || run.run_id}
      </span>
      <span className="font-mono text-annexe text-texte-secondaire">{run.run_id}</span>
      {href !== undefined && <LienRenvoi renvoi={{ href, libelle: "Voir le run" }} />}
    </p>
  );
}

export function GesteSurUnRun({
  demande,
  trancher,
  enCours = false,
}: {
  /** Le geste proposé, tel que le fil le porte. */
  demande: GesteRunPropose;
  /** Confirme (`true`) ou écarte (`false`) : rien d'autre ne part. */
  trancher: (approuve: boolean) => Promise<void>;
  /** Un échange est déjà en vol sur ce fil : les deux gestes se désarment. */
  enCours?: boolean;
}) {
  const [refus, setRefus] = useState<string | null>(null);
  const libelles = libellesDuGeste(demande.action);
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
      libelle="Geste sur un run"
      icone={GLYPHES[demande.action]}
      titre={libelles.question}
    >
      <CibleDuRun run={demande.run} />
      <p className="mt-2 text-corps text-texte">{consequenceDuGeste(demande)}</p>
      {/* Les bornes du nouveau run, **dans les deux sens** : « aucune » s'annonce
          comme un choix (#990), pas comme un oubli. */}
      {demande.action === "relance" && (
        <p className="mt-1 text-annexe text-texte-secondaire">
          Bornes du nouveau run : {phraseDesBornes(demande.bornes ?? AUCUNE_BORNE)}
        </p>
      )}
      <div className="mt-3 flex flex-wrap gap-2">
        <Bouton
          ton={demande.action === "annulation" ? "alerte" : "accent"}
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
 * L'état **relu** qu'une trace dit à la suite du geste : l'heure de la pause, l'état
 * d'un run repris, le nouveau run d'une relance. Rien pour une interruption : « Interrompu »
 * dit déjà tout, et « annulée » juste à côté le redirait.
 */
function etatRelu(fait: GesteRunFait): string {
  if (fait.nouveau !== null) {
    const etat = libelleStatutExecution(fait.nouveau.statut).toLowerCase();
    return `suivi par un nouveau run, ${etat}`;
  }
  if (fait.action === "annulation") return "";
  if (fait.run.en_pause) {
    const depuis = fait.run.pause_depuis ? formatHeureCourte(fait.run.pause_depuis) : "";
    return depuis ? `en pause depuis ${depuis}` : "en pause";
  }
  return libelleStatutExecution(fait.run.statut).toLowerCase();
}

/**
 * Ce qu'un geste confirmé a **donné**, sous la réponse : une ligne cochée — le geste
 * fait, l'état relu, le run —, ou le refus du service avec sa raison et le glyphe
 * d'arrêt. Le renvoi vers le run est celui de la ligne de faits (`Suite`).
 *
 * La coche d'une **interruption** est à la couleur du texte, pas au vert : le récit
 * de fin du run, juste au-dessus, dit « Run interrompu » au ton d'alerte, et un vert
 * à côté lisait le même état comme une réussite (relecture visuelle de #1179). La
 * coche dit que le geste a eu lieu ; le ton n'en fait pas une bonne nouvelle.
 */
export function TraceDuGeste({ fait }: { fait: GesteRunFait }) {
  const libelles = libellesDuGeste(fait.action);
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
  const cible = fait.nouveau ?? fait.run;
  const etat = etatRelu(fait);
  const ton = fait.action === "annulation" ? "text-texte" : "text-positif-texte";
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe">
      <span className={`inline-flex items-center gap-1 font-medium ${ton}`}>
        <IconeCoche className="size-3.5 shrink-0" />
        {libelles?.fait ?? "Fait"}
      </span>
      {etat !== "" && <span className="text-texte-secondaire">· {etat}</span>}
      <span className="font-mono text-texte-secondaire">{cible.run_id}</span>
    </p>
  );
}

/**
 * Les runs qu'une demande pouvait viser, quand elle en visait plusieurs — chacun à
 * ses faits, sans bouton : la question est dans les mots du modèle, juste au-dessus,
 * et la réponse se dit dans le composeur.
 *
 * Chaque candidat a son **encadré** — la carte compacte du socle : sous une bulle,
 * la ligne cible passe sur plusieurs rangs, et deux candidats collés se lisaient
 * comme un seul bloc dont seule la pastille du second marquait le début (relecture
 * visuelle de #1179).
 */
export function RunsCandidats({ runs }: { runs: RunVise[] }) {
  if (runs.length === 0) return null;
  return (
    <ul aria-label="Runs possibles" className="flex flex-col gap-2 text-annexe">
      {runs.map((run) => (
        <Carte key={run.run_id} balise="li" densite="compacte">
          <CibleDuRun run={run} />
        </Carte>
      ))}
    </ul>
  );
}

"use client";

/**
 * Le panneau des validations humaines (docs/05 §2.6, ticket #48) : chaque
 * demande en attente est mise en avant — action requise — avec le contexte
 * pour trancher (agent, tâche, action demandée, justification) et les boutons
 * **Approuver** / **Refuser** qui appellent
 * `POST /api/validations/{tache_id}/decision`. Le moteur, en pause sur la
 * demande, reprend la tâche ou l'annule proprement selon la décision.
 *
 * Depuis #227 une demande peut porter un **diff** (EF-37) : « appliquer ce
 * travail dans mon projet ? » est une action sensible de plus sur le même canal,
 * et c'est le diff — fichiers touchés, lignes ajoutées/supprimées, branche à
 * fusionner — qui remplace alors la description libre. Sans lui la question
 * serait une signature à l'aveugle ; le refus, lui, n'écrit rien et laisse le
 * travail consultable.
 *
 * Depuis #581 elle peut porter un **acte** — l'outil appelé et ses arguments —,
 * et c'est lui qui prend alors la tête de la carte. Le titre de la tâche ne
 * disparaît pas, il descend d'un cran : depuis que le déclencheur de l'arbitrage
 * est l'acte et non le texte de la tâche (#573), l'afficher en tête ferait lire
 * « Rédiger le README » au-dessus d'un `rm -rf` — la question qu'on pose n'est
 * plus celle-là. Une demande sans acte est rendue exactement comme avant.
 */

import { useState } from "react";

import { IconeAgent, IconeAlerte } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte, EnTeteSection } from "@/components/Primitives";
import { formatHeure } from "@/lib/format";
import {
  NATURE_AJOUT,
  NATURE_MODIFICATION,
  NATURE_SUPPRESSION,
  VALIDATION_EN_ATTENTE,
  type DiffProjet,
  type Validation,
} from "@/lib/types";

type Decider = (tacheId: string, approuve: boolean) => Promise<void>;

export function PanneauValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const enAttente = validations.filter((v) => v.statut === VALIDATION_EN_ATTENTE);
  if (enAttente.length === 0) return null;

  return (
    <Carte
      balise="section"
      ton="attention"
      data-guide="validations"
      aria-label="Validations en attente"
    >
      <EnTeteSection
        titre={
          <>
            Validations en attente
            <BadgeEtat ton="attention" className="chiffre">
              {enAttente.length}
            </BadgeEtat>
          </>
        }
        icone={IconeAlerte}
        ton="attention"
        className="mb-2"
      />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {enAttente.map((validation) => (
          <CarteValidation
            key={validation.tache_id}
            validation={validation}
            decider={decider}
          />
        ))}
      </div>
    </Carte>
  );
}

function CarteValidation({
  validation,
  decider,
}: {
  validation: Validation;
  decider: Decider;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surDecision = async (approuve: boolean) => {
    setEnCours(true);
    setErreur(null);
    try {
      await decider(validation.tache_id, approuve);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  // L'acte a la tête quand il y en a un (#581) ; sinon on retombe sur le titre
  // de la tâche, qui est ce que la demande portait de plus parlant avant #573.
  const acte = validation.outil;

  return (
    <Carte ton="attentionClaire" className="text-corps">
      <p className="font-medium" title={validation.tache_id}>
        {acte ? (
          <>
            <span className="text-neutral-500 dark:text-neutral-400">Appel de </span>
            <span className="font-mono">{acte}</span>
          </>
        ) : (
          validation.titre || validation.tache_id
        )}
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-1 text-annexe text-neutral-500 dark:text-neutral-400">
        <IconeAgent className="size-3.5 shrink-0" />
        Agent {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
        {validation.horodatage ? ` · ${formatHeure(validation.horodatage)}` : ""}
        {/* Le titre de la tâche reste lisible, une place plus bas : il dit d'où
            vient l'acte, il ne dit pas ce qu'on approuve. */}
        {acte && validation.titre ? ` · ${validation.titre}` : ""}
      </p>
      {validation.diff ? (
        <DiffApplication diff={validation.diff} />
      ) : acte ? (
        <ArgumentsActe arguments={validation.arguments} />
      ) : (
        validation.description && (
          <p className="mt-2 whitespace-pre-wrap text-annexe text-neutral-600 dark:text-neutral-300">
            {validation.description}
          </p>
        )
      )}
      {validation.raison && (
        <p className="mt-2 text-annexe italic text-amber-700 dark:text-amber-400">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <Bouton occupe={enCours} onClick={() => void surDecision(true)}>
          {enCours ? "Envoi…" : "Approuver"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="alerte"
          disabled={enCours}
          onClick={() => void surDecision(false)}
        >
          Refuser
        </Bouton>
      </div>
      {erreur && (
        <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </Carte>
  );
}

/**
 * Ce qu'on passe à l'outil (#581) : une ligne par argument, la clé puis sa
 * valeur. Le nom de l'outil n'y est pas répété — il est en tête de la carte,
 * c'est lui la question.
 *
 * La valeur est rendue **telle qu'elle a été composée**, sauts de ligne compris
 * (`whitespace-pre-wrap`) : un script passé à `Bash` aplati en une ligne se lit
 * autrement qu'il ne s'exécutera, et approuver ce qu'on lit mal n'est pas
 * approuver. Le backend l'a déjà bornée et expurgée (`maestro.acte`,
 * `evenement_demande`) — il n'y a donc rien à couper ici.
 *
 * Comme le diff, le bloc **défile** au-delà d'une poignée de lignes plutôt que
 * de pousser Approuver/Refuser hors de l'écran : une demande dont on ne voit
 * plus la réponse n'en est plus une.
 */
function ArgumentsActe({ arguments: args }: { arguments: Record<string, string> | null }) {
  const entrees = Object.entries(args ?? {});
  if (entrees.length === 0) {
    // Un outil sans paramètre existe, et un producteur qui n'en rapporte aucun
    // aussi : les deux se disent du même mot, aucun n'est une anomalie à signaler.
    return (
      <p className="mt-2 text-annexe italic text-neutral-500 dark:text-neutral-400">
        Aucun argument
      </p>
    );
  }
  return (
    <dl className="mt-2 max-h-48 overflow-y-auto rounded-md border border-neutral-200 bg-neutral-50 px-2 py-1.5 font-mono text-annexe dark:border-neutral-700 dark:bg-neutral-950/60">
      {entrees.map(([cle, valeur]) => (
        <div key={cle} className="flex items-baseline gap-2 py-0.5">
          <dt className="shrink-0 text-neutral-500 dark:text-neutral-400">{cle}</dt>
          <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-neutral-700 dark:text-neutral-300">
            {valeur}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Le diff d'une demande d'application dans le projet (#227, EF-37) : l'en-tête
 * dit ce qui se produirait à l'accord — fusion d'une branche pour un projet
 * versionné, écriture des fichiers sinon — puis chaque fichier avec ses lignes.
 *
 * Le tableau **défile** au-delà d'une quinzaine de fichiers plutôt que de
 * pousser les boutons Approuver/Refuser hors de l'écran : une demande de
 * validation dont on ne voit plus la réponse n'en est plus une.
 */
function DiffApplication({ diff }: { diff: DiffProjet }) {
  return (
    <div className="chiffre mt-2 rounded-md border border-neutral-200 bg-neutral-50 text-annexe dark:border-neutral-700 dark:bg-neutral-950/60">
      <p className="border-b border-neutral-200 px-2 py-1.5 text-neutral-700 dark:border-neutral-700 dark:text-neutral-300">
        <span className="font-medium">
          {diff.fichiers} fichier{diff.fichiers > 1 ? "s" : ""}
        </span>{" "}
        <span className="text-emerald-600 dark:text-emerald-400">+{diff.ajouts}</span>{" "}
        <span className="text-rose-600 dark:text-rose-400">−{diff.suppressions}</span>
        <br />
        <span className="text-neutral-500 dark:text-neutral-400">
          {diff.branche
            ? `Fusion de ${diff.branche} vers ${diff.base}`
            : "Écriture des fichiers dans le projet (non versionné)"}
        </span>
      </p>
      <ul className="max-h-48 overflow-y-auto px-2 py-1.5 font-mono">
        {diff.modifications.map((modification) => (
          <li key={modification.chemin} className="flex items-baseline gap-2 py-0.5">
            <span
              aria-hidden
              className={`w-3 shrink-0 text-center ${couleurNature(modification.nature)}`}
            >
              {SIGNE_NATURE[modification.nature] ?? "~"}
            </span>
            <span className="min-w-0 flex-1 break-all text-neutral-700 dark:text-neutral-300">
              {modification.chemin}
            </span>
            <span className="shrink-0 text-neutral-500 dark:text-neutral-400">
              {modification.binaire
                ? "binaire"
                : `+${modification.ajouts} −${modification.suppressions}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SIGNE_NATURE: Record<string, string> = {
  [NATURE_AJOUT]: "+",
  [NATURE_MODIFICATION]: "~",
  [NATURE_SUPPRESSION]: "−",
};

function couleurNature(nature: string): string {
  if (nature === NATURE_AJOUT) return "text-emerald-600 dark:text-emerald-400";
  if (nature === NATURE_SUPPRESSION) return "text-rose-600 dark:text-rose-400";
  return "text-amber-600 dark:text-amber-400";
}

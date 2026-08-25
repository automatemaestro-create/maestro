"use client";

/**
 * Le détail d'une tâche du Kanban (#251) : description, étapes en checklist et
 * liens utiles, ouverts **sur place** depuis la carte.
 *
 * Un panneau, pas une carte qui gonfle : c'est le point du ticket. La carte du
 * Kanban est un objet dense qu'on lit en diagonale sur cinq colonnes — y verser
 * une description et une checklist la rendrait illisible et casserait la borne
 * de hauteur des colonnes (#191). Le détail vit donc au-dessus du tableau de
 * bord, sur toute la hauteur à droite, et se referme sans quitter la page :
 * aucune navigation, la vue du run reste là où elle était.
 *
 * Le panneau est **modal** (`aria-modal`, voile) comme la visite guidée (#122) :
 * contrairement à l'assistant (#123), on ne le consulte pas *en même temps*
 * qu'on agit sur le Kanban — on ouvre une tâche, on la lit, on referme. Échap
 * ferme et rend le focus à la carte, patron canonique du dépôt.
 *
 * L'URL d'un lien vient du flux et non de l'UI : elle passe par `lienExterneSur`
 * (dans `detailDe`) avant de toucher un `href`, et un lien non suivable s'affiche
 * en texte — jamais de lien mort, même règle que le ticket externe (#192).
 */

import { useEffect, useRef } from "react";

import {
  AvancementEtapes,
  LigneEtape,
} from "@/components/EtapesTache";
import {
  IconeAgent,
  IconeDepot,
  IconeFermer,
  IconeLienExterne,
  IconeMaquette,
  IconeTicket,
} from "@/components/Icones";
import type { Icone } from "@/components/Primitives";
import { Infobulle } from "@/components/Infobulle";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import {
  SelecteurReassignation,
  type Reassigner,
} from "@/components/SelecteurReassignation";
import {
  detailDe,
  libelleDeNature,
  type LienAffiche,
  type NatureAffichee,
} from "@/lib/detailTache";
import { formatCout, libelleStatut } from "@/lib/format";
import { type EtatAgent, type Tache } from "@/lib/types";
import { usePiegeDeFocus } from "@/lib/usePiegeDeFocus";

/**
 * L'icône d'un lien, choisie sur sa **nature** — jamais devinée d'après l'URL,
 * qui ne dit rien d'une instance Figma/GitLab auto-hébergée.
 *
 * Des composants du jeu (#245) et non des émojis : ce panneau a été écrit avant
 * que le socle visuel ne soit posé, et il était le dernier écran à signer ses
 * lignes d'un 🎨 / 🎫 / 📦 / 🔗. Le vocabulaire reste celui des cartes —
 * `IconeTicket` y désigne déjà le ticket externe (#192).
 */
const ICONE_PAR_NATURE: Record<NatureAffichee, Icone> = {
  maquette: IconeMaquette,
  ticket: IconeTicket,
  depot: IconeDepot,
  lien: IconeLienExterne,
};

export function PanneauDetailTache({
  tache,
  agents,
  reassigner,
  fermer,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
  fermer: () => void;
}) {
  const panneau = useRef<HTMLDivElement>(null);
  const detail = detailDe(tache);
  const nom = tache.titre || tache.id;

  // Échap ferme. Le focus revient à la carte : c'est l'appelant qui le rend,
  // lui seul connaissant le déclencheur.
  useEffect(() => {
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === "Escape") fermer();
    };
    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [fermer]);

  // Le panneau prend le focus à l'ouverture, sans quoi Échap ne serait entendu
  // que par le document et la lecture d'écran resterait sur la carte.
  useEffect(() => panneau.current?.focus(), []);

  // La tabulation reste dans le panneau (#536) : il se déclare `aria-modal`, il
  // doit donc l'être pour le clavier aussi. Le composant n'est monté que quand
  // il est ouvert — le piège n'a pas de condition à recevoir.
  usePiegeDeFocus(panneau);

  return (
    <>
      <div
        aria-hidden="true"
        onClick={fermer}
        className="fixed inset-0 z-40 bg-slate-950/50"
      />
      <div
        ref={panneau}
        role="dialog"
        aria-modal="true"
        aria-label={`Détail de la tâche ${nom}`}
        tabIndex={-1}
        className={
          "fixed inset-y-0 right-0 z-50 flex w-[min(28rem,100vw)] flex-col overflow-hidden " +
          "border-l border-neutral-200 bg-white text-left shadow-2xl outline-none " +
          "dark:border-neutral-700 dark:bg-neutral-900"
        }
      >
        <header className="flex items-start gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
          <div className="min-w-0 flex-1">
            <h2 className="text-corps font-semibold text-neutral-900 dark:text-neutral-100">
              {nom}
            </h2>
            {/* « Agent » en toutes lettres derrière l'icône du jeu (#245) :
                l'émoji 🤖 portait seul l'information, et une tâche non assignée
                ne disait pas de quoi elle manquait. */}
            <p className="mt-0.5 flex flex-wrap items-center gap-1 text-annexe text-neutral-500 dark:text-neutral-400">
              <span>{libelleStatut(tache.statut)} ·</span>
              <IconeAgent className="size-3.5 shrink-0" />
              <span>
                Agent {tache.agent || "non assigné"}
                {tache.role ? ` · ${tache.role}` : ""} ·{" "}
                {formatCout(tache.cout_usd)}
              </span>
            </p>
            <LienTicketExterne
              reference={tache.ticket}
              tache={nom}
              className="mt-1"
            />
          </div>
          <button
            type="button"
            onClick={fermer}
            aria-label="Fermer le détail de la tâche"
            className="-mr-1 rounded-md p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-900 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
          >
            <IconeFermer className="size-4" />
          </button>
        </header>

        <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-4 py-3">
          {detail.description !== "" && (
            <section aria-label="Description">
              <TitreSection>Description</TitreSection>
              <p className="whitespace-pre-wrap break-words text-corps text-neutral-700 dark:text-neutral-300">
                {detail.description}
              </p>
            </section>
          )}

          {detail.etapes.length > 0 && (
            <section aria-label="Étapes">
              <TitreSection
                compteur={`${detail.faites}/${detail.etapes.length}`}
              >
                Étapes
              </TitreSection>
              <AvancementEtapes
                etapes={detail.etapes}
                faites={detail.faites}
              />
              <ul className="mt-2 space-y-1.5">
                {detail.etapes.map((etape, rang) => (
                  <LigneEtape key={`${rang}-${etape.libelle}`} etape={etape} />
                ))}
              </ul>
            </section>
          )}

          {detail.liens.length > 0 && (
            <section aria-label="Liens utiles">
              <TitreSection>Liens utiles</TitreSection>
              <ul className="space-y-1">
                {detail.liens.map((lien, rang) => (
                  <li key={`${rang}-${lien.libelle}`}>
                    <LigneLien lien={lien} />
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>

        {/* La réassignation reste celle de la carte (EF-11/EF-20), au même
            composant près : conclure « ce n'est pas pour cet agent » en lisant
            les étapes ne doit pas obliger à refermer le panneau. */}
        <footer className="border-t border-neutral-200 px-4 py-3 dark:border-neutral-800">
          <SelecteurReassignation
            tache={tache}
            agents={agents}
            reassigner={reassigner}
          />
        </footer>
      </div>
    </>
  );
}

function TitreSection({
  children,
  compteur,
}: {
  children: string;
  compteur?: string;
}) {
  return (
    <h3 className="mb-1.5 flex items-baseline gap-2 text-annexe font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
      {children}
      {compteur !== undefined && (
        <span className="font-normal normal-case tracking-normal text-neutral-400 dark:text-neutral-500">
          {compteur}
        </span>
      )}
    </h3>
  );
}

/** Un lien utile, rendu selon sa nature. Sans URL suivable : du texte. */
function LigneLien({ lien }: { lien: LienAffiche }) {
  const Glyphe = ICONE_PAR_NATURE[lien.nature];
  const nature = libelleDeNature(lien.nature).toLowerCase();
  const commun = "inline-flex max-w-full items-center gap-1.5 text-corps";

  if (lien.url === null) {
    return (
      <Infobulle
        texte={`${libelleDeNature(lien.nature)} — aucune URL exploitable`}
        className={`${commun} text-neutral-500 dark:text-neutral-400`}
      >
        <Glyphe className="size-4 shrink-0" />
        <span className="truncate">{lien.libelle}</span>
      </Infobulle>
    );
  }

  return (
    <a
      href={lien.url}
      target="_blank"
      rel="noopener noreferrer"
      // Pas de `title={lien.url}` (#536) : même raison qu'au ticket externe —
      // la destination d'un lien est déjà portée par `href`.
      aria-label={`Ouvrir ${nature} ${lien.libelle} dans un nouvel onglet`}
      className={`${commun} rounded text-sky-700 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:text-sky-300 dark:focus-visible:outline-sky-400`}
    >
      <Glyphe className="size-4 shrink-0" />
      <span className="truncate">{lien.libelle}</span>
      {/* Le « part vers l'extérieur », icône du jeu comme sur le ticket externe
          (#192) plutôt qu'une flèche de texte, dont le rendu varie par police. */}
      <IconeLienExterne className="size-3.5 shrink-0" />
    </a>
  );
}

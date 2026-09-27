"use client";

/**
 * Le détail d'une tâche du Kanban (#251) : sa vérification (#1177), description,
 * étapes en checklist et liens utiles, ouverts **sur place** depuis la carte.
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
  IconeStatutBloquee,
  IconeStatutEchec,
  IconeStatutTerminee,
  IconeTicket,
} from "@/components/Icones";
import {
  BadgeEtat,
  CIBLE_MINIMALE,
  classesCarte,
  type Icone,
  type TonBadge,
} from "@/components/Primitives";
import { Infobulle } from "@/components/Infobulle";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import {
  SelecteurReassignation,
  type Reassigner,
} from "@/components/SelecteurReassignation";
import {
  detailDe,
  libelleDeNature,
  type ConstatAffiche,
  type LienAffiche,
  type NatureAffichee,
  type VerificationAffichee,
} from "@/lib/detailTache";
import { formatCout, formatDuree, libelleStatut } from "@/lib/format";
import {
  CONSTAT_NON_JOUE,
  CONSTAT_NON_TENU,
  CONSTAT_TENU,
  VERIFICATION_IMPOSSIBLE,
  VERIFICATION_NON_TENUE,
  VERIFICATION_TENUE,
  type EtatAgent,
  type Tache,
} from "@/lib/types";
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
  soldee = false,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
  fermer: () => void;
  /**
   * La tâche est **soldée** (#1112) : une étape non cochée ne le sera plus, et
   * la liste le dit ligne à ligne — c'est la moitié « lesquelles » du partage
   * avec le nœud du graphe, qui ne dit que « combien ».
   *
   * Le verdict vient de l'appelant, qui le tient déjà de la table partagée des
   * compartiments (`lib/graphe.etatDuNoeud`) : le déduire ici du statut brut
   * serait une seconde lecture du même fait, et deux lectures finissent par
   * diverger. `false` par défaut — un appelant qui ne le passe pas rend
   * exactement le panneau d'avant ce ticket.
   */
  soldee?: boolean;
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
          {/* La vérification d'abord (#1177) : « a-t-elle tenu ? » est la
              question qu'on pose au panneau avant de lire ce qu'on lui
              demandait. Absente tant que la tâche n'a pas été vérifiée. */}
          {detail.verification !== null && (
            <BlocVerification verification={detail.verification} />
          )}
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
                soldee={soldee}
              />
              <ul className="mt-2 space-y-1.5">
                {detail.etapes.map((etape, rang) => (
                  <LigneEtape
                    key={`${rang}-${etape.libelle}`}
                    etape={etape}
                    soldee={soldee}
                  />
                ))}
              </ul>
            </section>
          )}

          {/* Le temps de la tâche, décomposé (#989). Monté **seulement** quand
              une attente a été mesurée non nulle : sans attente il n'y a rien à
              décomposer, et répéter ici la durée que la carte porte déjà
              ouvrirait un panneau sur chaque tâche mesurée.

              Le travail vient **en tête**, et c'est la réserve n°2 du regard
              neuf : une attente seule ne se rapporte à rien. La référence fait
              exactement cela (GitLab CI, bloc de faits d'un job : « Durée »
              puis « En file d'attente », étiquetées, empilées, même taille). */}
          {detail.attentes.length > 0 && (
            <section aria-label="Temps">
              <TitreSection>Temps</TitreSection>
              <dl className="space-y-1 text-corps">
                <LigneTemps
                  libelle="Travail"
                  dureeMs={detail.travailMs}
                  accentue
                />
                {detail.attentes.map((attente) => (
                  <LigneTemps
                    key={attente.libelle}
                    libelle={attente.libelle}
                    dureeMs={attente.dureeMs}
                  />
                ))}
              </dl>
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

/**
 * Une ligne du bloc de temps (#989) : son nom à gauche, sa durée à droite.
 *
 * `accentue` distingue le **travail** de ce qui l'a retardé, par la graisse et
 * non par la couleur : la ligne doit rester lisible pour qui ne sépare pas les
 * gris, et le filet d'accessibilité refuse un état porté par la seule teinte.
 */
function LigneTemps({
  libelle,
  dureeMs,
  accentue = false,
}: {
  libelle: string;
  dureeMs: number | null;
  accentue?: boolean;
}) {
  // Les tokens de la palette sémantique, et aucun `dark:` : les deux thèmes
  // viennent avec le token (apps/web/README.md, « La palette sémantique »), et
  // le résidu de couleurs écrites à la main ne peut que décroître (#895).
  const ton = accentue ? "font-medium text-texte" : "text-texte-secondaire";
  return (
    <div className="flex items-baseline justify-between gap-3">
      <dt className={ton}>{libelle}</dt>
      <dd className={`chiffre shrink-0 ${ton}`}>{formatDuree(dureeMs)}</dd>
    </div>
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

/**
 * Le verdict d'une vérification (#1177) : son ton, son mot, son glyphe. La forme
 * porte l'état autant que la couleur — ✓ cerclé, ✗, ⊘ se distinguent en noir et
 * blanc (#709) —, et les trois issues du moteur ont chacune la leur : « non
 * vérifiée » n'est pas un vert, et ne ressemble donc pas à « vérifiée ».
 */
const VERDICT: Record<
  VerificationAffichee["statut"],
  { ton: TonBadge; libelle: string; icone: Icone }
> = {
  [VERIFICATION_TENUE]: { ton: "positif", libelle: "Vérifiée", icone: IconeStatutTerminee },
  [VERIFICATION_NON_TENUE]: { ton: "alerte", libelle: "Non tenue", icone: IconeStatutEchec },
  [VERIFICATION_IMPOSSIBLE]: {
    ton: "attention",
    libelle: "Non vérifiée",
    icone: IconeStatutBloquee,
  },
};

/** Ce qu'un contrôle a constaté, en forme et en mots — le mot est lu, jamais la seule teinte. */
const CONSTAT: Record<
  ConstatAffiche["etat"],
  { libelle: string; icone: Icone; couleur: string }
> = {
  [CONSTAT_TENU]: { libelle: "tenu", icone: IconeStatutTerminee, couleur: "text-positif" },
  [CONSTAT_NON_TENU]: { libelle: "non tenu", icone: IconeStatutEchec, couleur: "text-alerte" },
  [CONSTAT_NON_JOUE]: {
    libelle: "non joué",
    icone: IconeStatutBloquee,
    couleur: "text-attention",
  },
};

/**
 * La vérification de la tâche (#1177) — **en tête du corps**, parce que c'est la
 * question qu'on pose au panneau d'abord : *cette tâche a-t-elle tenu ce qu'on
 * lui demandait ?* Variante retenue sur pièces par le regard neuf (commentaire
 * « ## Variante retenue » du ticket), contre un verdict dans l'en-tête et un
 * détail replié en bas.
 *
 * Le verdict vient compté (le compte avant la liste, comme le résumé d'un run
 * GitHub Actions), puis un critère par ligne. Ce qui **ne tient pas** montre sa
 * preuve d'office — la commande telle qu'elle a été jouée, préfixée `$` comme
 * sur un job GitLab, son code, et la fin de sa sortie ; ce qui **tient** tient en
 * une ligne dépliable, parce qu'une étape réussie n'a pas à pousser la
 * description hors de l'écran.
 */
function BlocVerification({ verification }: { verification: VerificationAffichee }) {
  const verdict = VERDICT[verification.statut];
  return (
    <section aria-label="Vérification">
      <TitreSection compteur={`${verification.tenus}/${verification.constats.length}`}>
        Vérification
      </TitreSection>
      <p className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe text-texte-secondaire">
        <BadgeEtat ton={verdict.ton} icone={verdict.icone}>
          {verdict.libelle}
        </BadgeEtat>
        {verification.livraison !== null && <span>livraison n° {verification.livraison}</span>}
        {verification.renvoi !== "" && <span>renvoyée par la QA</span>}
      </p>
      {verification.empechement !== "" && (
        <p className="mb-2 text-corps text-texte">{verification.empechement}</p>
      )}
      <ul className="space-y-2.5">
        {verification.constats.map((constat, rang) => (
          <LigneConstat key={`${rang}-${constat.critere}`} constat={constat} />
        ))}
      </ul>
    </section>
  );
}

/**
 * Un contrôle : son critère, son état, et ce qui le prouve.
 *
 * Le retour à la ligne coupe aux espaces (`break-words`), jamais au milieu d'un
 * nombre : « n==6618 » ne doit pas se lire « n==66 / 18 » (réserve du regard
 * neuf). La sortie d'une commande garde ses sauts de ligne, bornée en hauteur et
 * défilante — c'est déjà sa fin, là où elle dit pourquoi.
 */
function LigneConstat({ constat }: { constat: ConstatAffiche }) {
  const etat = CONSTAT[constat.etat];
  const Glyphe = etat.icone;
  const joue = constat.commande !== "";
  const commande = joue
    ? `$ ${constat.commande}${constat.code !== null ? ` → code ${constat.code}` : ""}`
    : "";
  return (
    <li className="flex items-start gap-2">
      <Glyphe aria-hidden="true" className={`mt-0.5 size-4 shrink-0 ${etat.couleur}`} />
      <div className="min-w-0 flex-1">
        <p className="break-words text-corps text-texte">
          {constat.critere}
          <span className="sr-only"> — {etat.libelle}</span>
        </p>
        {constat.etat === CONSTAT_TENU ? (
          <TraceRepliee
            texte={joue ? commande : constat.preuve && `lu dans le livrable : ${constat.preuve}`}
            fixe={joue}
          />
        ) : (
          <>
            {joue && (
              <p className="mt-0.5 break-words font-mono text-annexe text-texte-secondaire">
                {commande}
              </p>
            )}
            {constat.preuve !== "" &&
              (joue ? (
                <pre
                  className={classesCarte({
                    densite: "compacte",
                    ton: "creuse",
                    className:
                      "mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words font-mono text-annexe text-texte",
                  })}
                >
                  {constat.preuve}
                </pre>
              ) : (
                <p className="mt-0.5 break-words text-annexe text-texte-secondaire">
                  {constat.preuve}
                </p>
              ))}
          </>
        )}
      </div>
    </li>
  );
}

/**
 * Ce qui prouve un critère **tenu**, en une ligne qui se déplie — la commande
 * jouée, ou ce que la lecture a trouvé dans le livrable.
 *
 * Un `<details>` natif, comme les replis du fil (`chat/EtapesDuFil`) : ouvrable
 * au clavier, annoncé par les lecteurs d'écran, marqué par le triangle du
 * navigateur — ce qui se déplie le montre. Replié, le sommaire tient sur une
 * ligne ; ouvert, **le même texte** s'y déroule en entier, au lieu d'être répété
 * dessous.
 */
function TraceRepliee({ texte, fixe }: { texte: string; fixe: boolean }) {
  if (texte === "") return null;
  return (
    <details className="group mt-0.5 text-annexe text-texte-secondaire">
      <summary
        className={
          `${CIBLE_MINIMALE} cursor-pointer truncate group-open:overflow-visible ` +
          `group-open:whitespace-pre-wrap group-open:break-words ${fixe ? "font-mono" : ""}`
        }
      >
        {texte}
      </summary>
    </details>
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

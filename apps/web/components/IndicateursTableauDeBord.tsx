/**
 * Les indicateurs de tête du tableau de bord (#191, lot 2 de #189) : la réponse
 * à « où en est-on ? » en une rangée de tuiles, là où cinq panneaux de plein
 * format se disputaient l'écran.
 *
 * Chaque tuile est un **résumé qui renvoie** : le chiffre tient sur une ligne,
 * le détail vit dans la page dédiée, et le lien y mène explicitement — rien
 * n'est supprimé du produit, tout est rangé. Les chemins sont résolus par le
 * menu (`entreeParLibelle`), source unique de la navigation : une page qui
 * déménage emmène le renvoi avec elle.
 *
 * L'état vient du contexte partagé (`useEtatGlobal`, #117) via les props : ce
 * composant ne charge rien et ne décide de rien — il compte.
 */

import Link from "next/link";
import type { ReactNode } from "react";

import { formatCout } from "@/lib/format";
import { entreeParLibelle } from "@/lib/navigation";
import { AGENT_OCCUPE, type CoutExecution, type EtatAgent, type Tache } from "@/lib/types";

/**
 * Les statuts de tâche dont on a besoin ici (machine à états docs/03 §3, mêmes
 * colonnes que le Kanban). Redéclarés localement plutôt qu'importés du Kanban :
 * ce sont ses colonnes à lui, et les deux composants évoluent séparément.
 */
const STATUT_EN_COURS = "en_cours";
const STATUT_BLOQUEE = "bloquee";
const STATUT_TERMINEE = "terminee";
const STATUT_ECHEC = "echec";

/** Une tâche soldée ne compte plus dans ce qui est « en vol ». */
const STATUTS_SOLDES = new Set([STATUT_TERMINEE, STATUT_ECHEC]);

type Renvoi = { href: string; libelle: string };

type Indicateur = {
  libelle: string;
  /**
   * Le chiffre de la tuile. Un `ReactNode` et non une chaîne : une valeur peut
   * porter son unité (« 2 occupé(s) »), et l'unité se rend en petit pour que le
   * chiffre reste ce qu'on voit en premier.
   */
  valeur: ReactNode;
  detail: string;
  /** Rendu en chasse fixe : un identifiant d'exécution, pas un compte. */
  monospace?: boolean;
  /** Infobulle quand la valeur peut être tronquée. */
  titre?: string;
  renvoi?: Renvoi;
};

/** L'unité qui accompagne un chiffre : présente, mais pas au même niveau. */
const STYLE_UNITE = "text-sm font-normal text-neutral-500 dark:text-neutral-400";

export function IndicateursTableauDeBord({
  taches,
  agents,
  couts,
}: {
  taches: Tache[];
  agents: EtatAgent[];
  couts: CoutExecution[];
}) {
  const enVol = taches.filter((t) => !STATUTS_SOLDES.has(t.statut));
  const runsActifs = [
    ...new Set(enVol.map((t) => t.run_id).filter(Boolean)),
  ];
  const compte = (statut: string) =>
    taches.filter((t) => t.statut === statut).length;

  // Ce qu'on vient chercher sur cette tuile, c'est « combien travaillent,
  // combien sont disponibles » (#247) — pas un ratio d'agents allumés. Le
  // décompte porte sur les agents **actifs** : un agent désactivé ne reçoit plus
  // de tâche, il n'est donc ni occupé ni libre, il est hors capacité. C'est le
  // détail qui le dit.
  const actifs = agents.filter((a) => a.actif);
  const occupes = actifs.filter((a) => a.statut === AGENT_OCCUPE).length;
  const libres = actifs.length - occupes;
  const desactives = agents.length - actifs.length;

  // Somme des grands livres (#57) plutôt que des coûts rapportés par agent : le
  // grand livre porte AUSSI la part de planification (l'orchestrateur), qui
  // n'est attribuée à aucun agent. D'où un total légèrement supérieur au « coût
  // cumulé » de la barre supérieure — le détail de la tuile le dit, pour que
  // l'écart se lise au lieu de passer pour une incohérence. Aucun coût rapporté
  // ≠ coût nul : `formatCout` rend « — ».
  const montants = couts
    .map((c) => c.total.cout_usd)
    .filter((c): c is number => c !== null);
  const depense =
    montants.length > 0 ? montants.reduce((somme, c) => somme + c, 0) : null;

  const pageAgents = entreeParLibelle("Agents");
  const pageCouts = entreeParLibelle("Coûts & analytics");

  const indicateurs: Indicateur[] = [
    {
      libelle: "Run en cours",
      valeur:
        runsActifs.length === 0
          ? "Aucun"
          : runsActifs.length === 1
            ? runsActifs[0]
            : `${runsActifs.length} runs`,
      monospace: runsActifs.length === 1,
      titre: runsActifs.length === 1 ? runsActifs[0] : undefined,
      detail:
        enVol.length === 0
          ? taches.length === 0
            ? "aucune tâche connue"
            : "toutes les tâches sont soldées"
          : `${enVol.length} tâche(s) encore ouverte(s)`,
    },
    {
      libelle: "Tâches",
      valeur: String(taches.length),
      detail: `${compte(STATUT_EN_COURS)} en cours · ${compte(STATUT_BLOQUEE)} bloquée(s) · ${compte(STATUT_ECHEC)} échec(s)`,
    },
    {
      libelle: "Agents",
      valeur: (
        <>
          {occupes}
          <span className={STYLE_UNITE}> occupé(s) · </span>
          {libres}
          <span className={STYLE_UNITE}> libre(s)</span>
        </>
      ),
      titre: `${occupes} occupé(s) · ${libres} libre(s)`,
      detail:
        agents.length === 0
          ? "aucun agent connu"
          : `${agents.length} au total · ${desactives} désactivé(s)`,
      renvoi: pageAgents && {
        href: pageAgents.href,
        libelle: "Voir les agents",
      },
    },
    {
      libelle: "Dépense",
      valeur: formatCout(depense),
      detail: `${couts.length} exécution(s), planification comprise`,
      renvoi: pageCouts && {
        href: pageCouts.href,
        libelle: "Détail par période",
      },
    },
  ];

  return (
    <section
      data-guide="indicateurs"
      aria-label="Indicateurs de tête"
      /* Colonnes calées sur la largeur de la zone de contenu (#117), pas sur
         celle de la fenêtre : la sidebar en prend une part variable.
         Rangée unique dès `@3xl` (48 rem) et non plus `@4xl` (#248) : les
         tuiles rendent une rangée entière au tableau des tâches, qui prend
         désormais la hauteur restante. */
      className="grid grid-cols-1 gap-3 @sm:grid-cols-2 @3xl:grid-cols-4"
    >
      {indicateurs.map((indicateur) => (
        <Tuile key={indicateur.libelle} indicateur={indicateur} />
      ))}
    </section>
  );
}

/**
 * Une tuile, **resserrée** par #248 : le tableau des tâches prend désormais la
 * hauteur que la page lui laisse, donc tout ce que cette rangée garde en
 * hauteur, il le perd. Le chiffre reste ce qu'on voit en premier — c'est
 * l'espace autour de lui qui se réduit, pas lui.
 */
function Tuile({ indicateur }: { indicateur: Indicateur }) {
  return (
    <article className="flex flex-col rounded-lg border border-neutral-200 bg-white px-3 py-2 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        {indicateur.libelle}
      </p>
      <p
        className={
          "mt-0.5 truncate font-semibold " +
          (indicateur.monospace ? "font-mono text-sm" : "text-xl")
        }
        title={indicateur.titre}
      >
        {indicateur.valeur}
      </p>
      <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
        {indicateur.detail}
      </p>
      {indicateur.renvoi && (
        <Link
          href={indicateur.renvoi.href}
          className="mt-1.5 text-xs font-medium text-sky-700 hover:underline dark:text-sky-400"
        >
          {indicateur.renvoi.libelle} →
        </Link>
      )}
    </article>
  );
}

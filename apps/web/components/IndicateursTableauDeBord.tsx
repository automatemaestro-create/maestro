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
 *
 * **Ce qu'il compte est celui du projet actif** (#281). `taches` et `couts`
 * arrivent déjà filtrés (`?projet=`, #277) : les tuiles « Run en cours »,
 * « Tâches » et « Dépense » sont cadrées par construction. La tuile « Agents »,
 * elle, l'est par ce composant, parce que le **parc est celui du poste** et non
 * du projet (docs/05 §2.3) : voir sa construction plus bas.
 */

import type { ReactNode } from "react";

import {
  IconeAgents,
  IconeMonnaie,
  IconeStatutEnCours,
  IconeTache,
} from "@/components/Icones";
import { type Icone, type Renvoi, TuileChiffre } from "@/components/Primitives";
import { coutCumule } from "@/lib/etatGlobal";
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

type Indicateur = {
  libelle: string;
  /**
   * Le chiffre de la tuile. Un `ReactNode` et non une chaîne : une valeur peut
   * porter son unité (« 2 occupé(s) »), et l'unité se rend en petit pour que le
   * chiffre reste ce qu'on voit en premier.
   */
  valeur: ReactNode;
  detail: string;
  icone: Icone;
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
  //
  // #281 y ajoute le cadre : le parc est celui du **poste**, partagé par tous
  // les projets (`GET /api/agents` ne porte pas de portée, et c'est une
  // décision — docs/05 §2.3). Compter les occupés au global sur un tableau de
  // bord cadré sur un projet ferait de cette tuile la seule à parler d'ailleurs.
  // On la découpe donc en trois chiffres dont chacun dit d'où il vient : **au
  // travail sur ce projet** (dérivé de ses tâches, donc strictement à lui),
  // **libres** (l'être ne dépend d'aucun projet — un agent libre l'est aussi
  // pour celui-ci), et **occupés ailleurs**, renvoyés au détail avec le parc.
  //
  // Le croisement se fait sur les **identifiants** de `taches_en_cours` et non
  // sur le nom de l'agent : c'est la donnée dont le backend dérive lui-même
  // `occupe` (maestro/controltower/state.py), donc `ici` ne peut pas dépasser
  // `occupes` ni dépendre de la façon dont on devine « travailler ». Un agent à
  // plusieurs instances (#86) tenant deux tâches de deux projets compte pour un
  // ici, ce qui est la bonne réponse à « qui travaille sur ce projet ».
  const actifs = agents.filter((a) => a.actif);
  const idsDuProjet = new Set(taches.map((t) => t.id));
  const ici = actifs.filter((a) =>
    a.taches_en_cours.some((id) => idsDuProjet.has(id)),
  ).length;
  const occupes = actifs.filter((a) => a.statut === AGENT_OCCUPE).length;
  const ailleurs = occupes - ici;
  const libres = actifs.length - occupes;
  const desactives = agents.length - actifs.length;

  // Somme des grands livres (#57) plutôt que des coûts rapportés par agent : le
  // grand livre porte AUSSI la part de planification (l'orchestrateur), qui
  // n'est attribuée à aucun agent — et, depuis #281, il est le seul des deux à
  // être **cadré sur le projet**, les coûts par agent valant pour le poste
  // entier. La barre supérieure est passée à la même source (`coutCumule`) :
  // les deux montants s'accordent désormais au lieu d'afficher un écart à
  // expliquer. Aucun coût rapporté ≠ coût nul : `formatCout` rend « — ».
  const depense = coutCumule(couts);

  const pageAgents = entreeParLibelle("Agents");
  const pageCouts = entreeParLibelle("Coûts & analytics");

  const indicateurs: Indicateur[] = [
    {
      libelle: "Run en cours",
      icone: IconeStatutEnCours,
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
      icone: IconeTache,
      valeur: String(taches.length),
      detail: `${compte(STATUT_EN_COURS)} en cours · ${compte(STATUT_BLOQUEE)} bloquée(s) · ${compte(STATUT_ECHEC)} échec(s)`,
    },
    {
      libelle: "Agents",
      icone: IconeAgents,
      valeur: (
        <>
          {ici}
          <span className={STYLE_UNITE}> sur ce projet · </span>
          {libres}
          <span className={STYLE_UNITE}> libre(s)</span>
        </>
      ),
      titre: `${ici} au travail sur ce projet · ${libres} libre(s)`,
      detail:
        agents.length === 0
          ? "aucun agent connu"
          : `${agents.length} agent(s) du poste · ${ailleurs} occupé(s) ailleurs · ${desactives} désactivé(s)`,
      renvoi: pageAgents && {
        href: pageAgents.href,
        libelle: "Voir les agents",
      },
    },
    {
      libelle: "Dépense",
      icone: IconeMonnaie,
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
        <TuileChiffre key={indicateur.libelle} {...indicateur} />
      ))}
    </section>
  );
}

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
 *
 * ⚠ **La tuile « Run en cours » ne se dérive plus des tâches** (#927, lot 6 de
 * #921) : elle reçoit les runs que l'écran a rangés (`quiTournent`), c'est-à-dire
 * la même carte que le bloc du run au centre et que l'état des runs. Elle les
 * dérivait des `taches` du projet, et c'est le constat **G2** du retex du
 * 2026-09-11 : « Aucun » affiché juste au-dessus d'une section qui disait
 * « EN COURS 1 ». Deux sources pour un fait ne se synchronisent pas, elles se
 * remplacent par une — c'est la leçon que #365 a tirée du cycle de vie d'un
 * ticket, appliquée ici à un écran.
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
import { estEnDecomposition } from "@/lib/execution";
import { formatCout } from "@/lib/format";
import { entreeParLibelle } from "@/lib/navigation";
import {
  AGENT_OCCUPE,
  type CoutExecution,
  type EtatAgent,
  type ResumeExecution,
  type Tache,
} from "@/lib/types";

/**
 * Les statuts de tâche dont on a besoin ici (machine à états docs/03 §3, mêmes
 * colonnes que le Kanban). Redéclarés localement plutôt qu'importés du Kanban :
 * ce sont ses colonnes à lui, et les deux composants évoluent séparément.
 */
const STATUT_EN_COURS = "en_cours";
const STATUT_BLOQUEE = "bloquee";
const STATUT_ECHEC = "echec";

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
  quiTournent = [],
}: {
  taches: Tache[];
  agents: EtatAgent[];
  couts: CoutExecution[];
  /**
   * Les runs **qui travaillent**, tels que l'écran les a rangés (#927) —
   * `runsParRegime(…).get(REGIME_TRAVAILLE)`, la carte dont `EtatDesRuns` et le
   * run mis au centre sortent eux aussi.
   *
   * C'est **le** remède à G2 (retex du 2026-09-11) : la tuile dérivait ses runs
   * des `taches` du projet, pendant que la section juste dessous lisait les
   * `executions`. Les deux lectures ne pouvaient qu'être d'accord par accident,
   * et pendant la décomposition elles ne l'étaient pas du tout — zéro tâche
   * contre un run en vol, donc « Aucun » au-dessus de « EN COURS 1 ». Le remède
   * est **une source**, pas une synchronisation : la liste arrive déjà rangée,
   * cette tuile ne la redéduit pas.
   *
   * Défaut vide plutôt qu'obligatoire : un appelant qui ne connaît pas les runs
   * — un banc de primitives, un test de tuile — rend alors « Aucun », ce qui est
   * vrai de ce qu'on lui a donné.
   */
  quiTournent?: ResumeExecution[];
}) {
  const compte = (statut: string) =>
    taches.filter((t) => t.statut === statut).length;

  // Ce que la tuile dit **sous** le chiffre. Les tâches encore ouvertes viennent
  // de la progression des runs eux-mêmes (#473, comptée par le backend sur la
  // machine à états) et non des `taches` chargées : c'est la même source que la
  // valeur au-dessus, et c'est tout l'objet du lot. `nb_taches` en repli pour un
  // résumé servi sans progression.
  const ouvertes = quiTournent.reduce(
    (total, run) =>
      total +
      (run.progression
        ? run.progression.total - run.progression.soldees
        : run.nb_taches),
    0,
  );
  // La décomposition **dite** en tête d'écran (#927, G11) : pendant les premières
  // minutes d'un run il n'y a aucune tâche à compter, et « 0 tâche(s) encore
  // ouverte(s) » se lirait « il ne se passe rien » à l'instant précis où tout se
  // passe. La condition porte sur **tous** les runs en vol : dès que l'un d'eux a
  // un plan, il y a bien quelque chose à compter.
  const decomposent =
    quiTournent.length > 0 && quiTournent.every(estEnDecomposition);

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
  // ⚠ **Ce parc ne porte plus l'orchestration** (#1028) : elle dépense et on lui
  // parle, mais elle n'exécute aucune tâche, donc elle n'est pas un membre du
  // parc (docs/37 §4.2). Jusque-là `GET /api/agents` la rendait — la tuile
  // annonçait « 6 agent(s) du poste » quand `/agents` en listait 5 (constat C13
  // du retex du 2026-09-11). Le remède est **à la source** et non ici : ce
  // composant continue de compter ce qu'on lui donne, et ce qu'on lui donne est
  // désormais la même population que celle de `/agents`.
  //
  // Le détail le **dit** plutôt que de laisser le compte passer de 6 à 5 sans
  // explication — « hors orchestration ». Deux points de rédaction, tous deux
  // relevés à la relecture visuelle de #1028 et tous deux contraints :
  //
  // - **la virgule n'est pas un ornement.** Séparée par un « · » comme les
  //   deux membres suivants, l'exclusion se lisait comme un troisième état
  //   d'occupation (« occupés ailleurs », « désactivés », « hors
  //   orchestration ») ; la virgule la rattache au **compte**, qui est ce
  //   qu'elle qualifie ;
  // - **le « du poste » d'avant a été cédé.** Le détail doit tenir sur **deux
  //   lignes** — une tuile plus haute que ses trois voisines se voit dans une
  //   rangée —, et la forme qui garde les deux mentions en fait trois. Le
  //   cadre du poste n'est pas perdu pour autant : « occupé(s) ailleurs » ne
  //   veut rien dire d'autre qu'un parc partagé entre projets, et la valeur de
  //   tête dit déjà « sur ce projet ».
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
        quiTournent.length === 0
          ? "Aucun"
          : quiTournent.length === 1
            ? quiTournent[0].run_id
            : `${quiTournent.length} runs`,
      monospace: quiTournent.length === 1,
      titre: quiTournent.length === 1 ? quiTournent[0].run_id : undefined,
      detail:
        quiTournent.length === 0
          ? "aucun run ne travaille en ce moment"
          : decomposent
            ? "décomposition en cours"
            : `${ouvertes} tâche(s) encore ouverte(s)`,
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
          : `${agents.length} agent(s), hors orchestration · ${ailleurs} occupé(s) ailleurs · ${desactives} désactivé(s)`,
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

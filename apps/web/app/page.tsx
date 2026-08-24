"use client";

/**
 * Le tableau de bord de la Control Tower — épuré par #191 (lot 2 de #189).
 *
 * Il répond à « où en est-on, et qu'est-ce qui m'attend ? » en un écran, dans
 * cet ordre : **ce qui attend un arbitrage humain** (#48), les **indicateurs de
 * tête** (run en cours, tâches par statut, agents, dépense), **l'état des runs**
 * (#476) et un **aperçu** de l'activité en direct.
 *
 * Ce qui en est parti n'a pas disparu, il est rangé — et chaque tuile renvoie
 * vers sa page : les fiches d'agent (statut, capacité, coût par agent) vers
 * Agents, Paramètres › Agents et Coûts & analytics, le grand livre par exécution
 * (#57, #58) vers Coûts & analytics. Le fil d'activité a rejoint la liste
 * depuis #249 : il tient en plein format au Journal, ne reste ici qu'en aperçu,
 * et son lien s'est allumé de lui-même le jour où la page est entrée au menu.
 *
 * **Le Kanban en est parti à son tour** (#476, renverse #248 — revue #470,
 * docs/29 §3). Il *était* l'objet de cet écran et en prenait toute la hauteur ; ce
 * qu'on lui reproche n'est pas sa place mais sa **portée** — il rend les tâches du
 * projet (#277/#281), donc ce qui court mêlé à ce qui est fini depuis trois jours,
 * là où « où en est-on ? » porte sur ce qui tourne, c'est-à-dire un **run**. Il
 * reparaît entier dans la vue d'un run (#475), et `EtatDesRuns` prend sa place ici.
 *
 * **Plus rien ne s'étire donc sur cette page**, et c'est le seul effet de mise en
 * page du lot : le `<main>` du shell est une colonne flex qui occupe au moins la
 * fenêtre (#117) et ces sections en sont les enfants directs — le fragment ci-dessous
 * ne crée aucun nœud. Toutes prennent maintenant la hauteur de leur contenu, la
 * chaîne `min-h-0 flex-1` de #248 partant avec le Kanban qui la portait, ainsi que
 * la borne `max-h-96` que #191 lui avait posée. Il n'y a rien à répartir en
 * remplacement : l'état des runs est une liste, une liste se lit du haut, et lui
 * donner tout l'écran ne ferait qu'étirer du vide les jours calmes.
 *
 * L'état vient du contexte partagé du shell (#117) : ce lot réorganise
 * l'affichage, la mécanique temps réel (WebSocket, rechargements coalescés) est
 * inchangée.
 *
 * Cas particulier depuis #186 : **rien à montrer**. Le lanceur local démarre
 * désormais en mode réel, où un premier écran est légitimement vide — il porte
 * alors `PosteVide`, qui dit quoi faire, plutôt que quatre panneaux à zéro.
 * Depuis #281 ce vide est celui **d'un projet** : tout ce que la page rend est
 * cadré sur le projet actif, et `PosteVide` le nomme au lieu de laisser croire
 * que rien ne tourne nulle part.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { IndicateursTableauDeBord } from "@/components/IndicateursTableauDeBord";
import { PanneauBriefs } from "@/components/PanneauBriefs";
import { PanneauRunsPerdus } from "@/components/PanneauRunsPerdus";
import { PanneauValidations } from "@/components/PanneauValidations";
import { PosteVide } from "@/components/PosteVide";
import { EtatDesRuns } from "@/components/runs/EtatDesRuns";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle } from "@/lib/navigation";

/**
 * Longueur de l'aperçu d'activité. Assez pour voir le flux vivre, assez court
 * pour que l'ensemble tienne sous la ligne de flottaison d'un portable.
 *
 * Cet aperçu-ci reste celui du **direct** (`MAX_EVENEMENTS` côté client) : c'est
 * la page Journal qui, depuis #478, part de l'historique persisté et le renvoi
 * ci-dessous y mène. Un tableau de bord qui relirait le journal à son tour paierait
 * une lecture de plus pour six lignes.
 */
const APERCU_ACTIVITE = 6;

export default function TableauDeBord() {
  const {
    projet,
    taches,
    agents,
    evenements,
    validations,
    executions,
    couts,
    connecte,
    chargement,
    erreur,
    decider,
    relancerRun,
  } = useEtatGlobal();

  const journal = entreeParLibelle("Journal");

  // Rien reçu **sur ce projet**, et l'API répond : le poste n'est pas en panne,
  // il n'a pas encore de run à montrer ici (#186 — le mode réel est désormais le
  // défaut du lanceur local ; #281 — la portée est celle du projet actif). On
  // explique quoi faire au lieu d'aligner quatre panneaux vides. Une API
  // injoignable, elle, garde les panneaux : sa bannière dit déjà le problème, et
  // conseiller « lancez un run » serait alors un contresens.
  //
  // Les **exécutions** entrent dans le compte depuis #322, et ce n'était pas un
  // oubli anodin : un run arrêté sur son brief ne crée aucune tâche, n'ouvre
  // aucune validation, et l'événement qui l'a suspendu n'atteint pas une Control
  // Tower cadrée sur un projet. Un premier run mené depuis « Composer un
  // objectif » atterrissait donc sur « rien à regarder » — l'écran qui conseille
  // de lancer un run, affiché à quelqu'un dont le run attend justement qu'on le
  // regarde.
  const rienARegarder =
    erreur === null &&
    taches.length === 0 &&
    evenements.length === 0 &&
    validations.length === 0 &&
    executions.length === 0;

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement de l&apos;état…</p>
      ) : rienARegarder ? (
        <PosteVide projet={projet} connecte={connecte} />
      ) : (
        <>
          {/* Avant les validations : un brief suspendu bloque le run entier,
              une validation ne retient qu'une tâche. */}
          <PanneauBriefs executions={executions} />
          <PanneauValidations validations={validations} decider={decider} />
          {/* Après les deux : ceux-là retiennent du travail **vivant**, un run
              perdu ne retient plus rien — son hôte est tombé. L'urgence n'est pas
              la même, et ce qui attend quelqu'un passe avant ce qui l'attendait
              (#349). */}
          <PanneauRunsPerdus executions={executions} relancer={relancerRun} />
          <IndicateursTableauDeBord
            taches={taches}
            agents={agents}
            couts={couts}
          />
          {/* Là où le Kanban prenait toute la hauteur (#248) : l'état des runs
              (#476). Il ne décide de rien — les trois panneaux au-dessus portent
              les gestes, celui-ci porte l'état, et un run qui attend paraît donc
              aux deux endroits. */}
          <EtatDesRuns
            executions={executions}
            validations={validations}
            taches={taches}
            projet={projet}
          />
          <FilActivite
            evenements={evenements}
            limite={APERCU_ACTIVITE}
            renvoi={
              journal && { href: journal.href, libelle: "Ouvrir le journal" }
            }
          />
        </>
      )}
    </>
  );
}

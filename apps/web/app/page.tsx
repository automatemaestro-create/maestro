"use client";

/**
 * Le tableau de bord de la Control Tower — épuré par #191 (lot 2 de #189).
 *
 * Il répond à « où en est-on, et qu'est-ce qui m'attend ? » en un écran, dans
 * cet ordre : **ce qui attend un arbitrage humain** (#48), les **indicateurs de
 * tête** (run en cours, tâches par statut, agents, dépense), le **Kanban** des
 * tâches (#47) et un **aperçu** de l'activité en direct.
 *
 * Ce qui en est parti n'a pas disparu, il est rangé — et chaque tuile renvoie
 * vers sa page : les fiches d'agent (statut, capacité, coût par agent) vers
 * Agents, Paramètres › Agents et Coûts & analytics, le grand livre par exécution
 * (#57, #58) vers Coûts & analytics. Le fil d'activité a rejoint la liste
 * depuis #249 : il tient en plein format au Journal, ne reste ici qu'en aperçu,
 * et son lien s'est allumé de lui-même le jour où la page est entrée au menu.
 *
 * **La hauteur va aux tâches** (#248). Le `<main>` du shell est une colonne flex
 * qui occupe au moins la fenêtre (#117) et ces sections en sont les enfants
 * directs — le fragment ci-dessous ne crée aucun nœud. Trois d'entre elles
 * prennent la hauteur de leur contenu ; le Kanban, lui, prend tout le reste et
 * fait défiler chaque colonne chez elle. C'est là toute la mise en page : rien
 * à répartir ici, le seul contrat est que ce composant reste **le seul** à
 * s'étirer — deux enfants extensibles se partageraient la place et aucun des
 * deux n'aurait la sienne.
 *
 * L'état vient du contexte partagé du shell (#117) : ce lot réorganise
 * l'affichage, la mécanique temps réel (WebSocket, rechargements coalescés) est
 * inchangée.
 *
 * Cas particulier depuis #186 : **rien à montrer**. Le lanceur local démarre
 * désormais en mode réel, où un premier écran est légitimement vide — il porte
 * alors `PosteVide`, qui dit quoi faire, plutôt que quatre panneaux à zéro.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { IndicateursTableauDeBord } from "@/components/IndicateursTableauDeBord";
import { Kanban } from "@/components/Kanban";
import { PanneauValidations } from "@/components/PanneauValidations";
import { PosteVide } from "@/components/PosteVide";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle } from "@/lib/navigation";

/**
 * Longueur de l'aperçu d'activité. Assez pour voir le flux vivre, assez court
 * pour que l'ensemble tienne sous la ligne de flottaison d'un portable (le fil
 * complet en garde 50 côté client — `MAX_EVENEMENTS`).
 */
const APERCU_ACTIVITE = 6;

export default function TableauDeBord() {
  const {
    taches,
    agents,
    evenements,
    validations,
    couts,
    connecte,
    chargement,
    erreur,
    reassigner,
    decider,
  } = useEtatGlobal();

  const journal = entreeParLibelle("Journal");

  // Rien reçu, et l'API répond : le poste n'est pas en panne, il n'a pas encore
  // de run à montrer (#186 — le mode réel est désormais le défaut du lanceur
  // local). On explique quoi faire au lieu d'aligner quatre panneaux vides.
  // Une API injoignable, elle, garde les panneaux : sa bannière dit déjà le
  // problème, et conseiller « lancez un run » serait alors un contresens.
  const rienARegarder =
    erreur === null &&
    taches.length === 0 &&
    evenements.length === 0 &&
    validations.length === 0;

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement de l&apos;état…</p>
      ) : rienARegarder ? (
        <PosteVide connecte={connecte} />
      ) : (
        <>
          <PanneauValidations validations={validations} decider={decider} />
          <IndicateursTableauDeBord
            taches={taches}
            agents={agents}
            couts={couts}
          />
          <Kanban taches={taches} agents={agents} reassigner={reassigner} />
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

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
 * (#57, #58) vers Coûts & analytics. Le fil d'activité, lui, n'a pas encore de
 * page : il reste ici en aperçu jusqu'à ce que le Journal du chantier
 * « Visibilité » entre au menu, où son lien s'allumera tout seul.
 *
 * L'état vient du contexte partagé du shell (#117) : ce lot réorganise
 * l'affichage, la mécanique temps réel (WebSocket, rechargements coalescés) est
 * inchangée.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { IndicateursTableauDeBord } from "@/components/IndicateursTableauDeBord";
import { Kanban } from "@/components/Kanban";
import { PanneauValidations } from "@/components/PanneauValidations";
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
    chargement,
    erreur,
    reassigner,
    decider,
  } = useEtatGlobal();

  const journal = entreeParLibelle("Journal");

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {chargement ? (
        <p className="text-sm text-neutral-500">Chargement de l&apos;état…</p>
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

/**
 * Le harnais des écrans du menu : quel composant chaque route rend, dans quel
 * état l'état partagé les met, et comment les monter dans leur shell réel.
 *
 * ⚠ Le nombre n'est **pas** écrit ici, et depuis #270 il n'est plus écrit nulle
 * part : la table est confrontée à `MENU` par les deux suites qui s'en servent
 * (`a11y`, `sobriete`), donc c'est `MENU` qui fait foi. Il y en a eu dix de #537
 * à #270 ; les compter en prose était le plus court chemin vers une doc fausse
 * au prochain écran — c'est le reproche que docs/30 §4.2 fait déjà au tableau
 * compté à la main. #484 l'a confirmé aussitôt, dans l'autre sens : « Composer
 * un objectif » et « Valider le brief » ont quitté le menu, donc cette table.
 *
 * Leur retrait d'ici n'est d'ailleurs pas un choix mais la **conséquence** de ce
 * que la table est dérivée : leurs écrans redirigent (`next.config.ts`), plus
 * personne ne les atteint, et les auditer reviendrait à rendre un verdict sur
 * une page que le produit ne sert plus. Leurs composants, eux, restent couverts
 * là où ils vivent encore — `brief/` par le fil du cadrage (#483) et par
 * `brief.test.tsx`, `composer/` par `composer.test.tsx` et
 * `composer-sources.test.tsx`.
 *
 * Il est né avec le filet d'accessibilité (#537) et vit ici depuis #539, quand
 * la sonde de sobriété a eu besoin des mêmes écrans dans le même état. Deux
 * harnais à tenir d'accord seraient le premier moyen pour qu'une suite rende un
 * verdict sur un produit que l'autre ne monte plus — c'est la leçon de
 * `tests/harnais_forge.py` côté outillage, et elle vaut ici.
 *
 * Ce que ce module **ne porte pas** : les fabriques de mock, qui vivent dans
 * `ecrans-reseau.ts` et y restent pour une raison de fond (voir son en-tête —
 * une factory `vi.mock` qui importerait une page se rappellerait elle-même).
 */

import { act, render, screen } from "@testing-library/react";

import { Shell } from "@/components/Shell";
import { ListeAgents } from "@/components/ListeAgents";
import { ongletAgentOuDefaut } from "@/lib/agents";
import {
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_TERMINEE,
  VALIDATION_APPROUVEE,
} from "@/lib/types";

import PageTableauDeBord from "@/app/page";
import PageChat from "@/app/chat/page";
import PageCouts from "@/app/couts/page";
import PageIntegrations from "@/app/integrations/page";
import PageJournalEcran from "@/app/journal/page";
import PageParametres from "@/app/parametres/page";
import PageRuns from "@/app/runs/page";
import PageValidations from "@/app/validations/page";

import {
  agentFactice,
  coutExecutionFactice,
  entreeJournalFactice,
  evenementFactice,
  poserChemin,
  poserEtatGlobal,
  poserJournal,
  runFactice,
  tacheFactice,
  usageFactice,
  validationFactice,
} from "./aides";

// --- L'état partagé, peuplé -------------------------------------------------

/** Ce que les deux états ci-dessous ont en commun : un projet qui travaille. */
function socleDuProjet() {
  return {
    taches: [
      tacheFactice({ id: "T-1", statut: "en_cours", titre: "Écrire les tests" }),
      tacheFactice({
        id: "T-2",
        statut: "terminee",
        titre: "Poser le schéma",
        agent: "qa",
        cout_usd: 0.42,
      }),
      tacheFactice({ id: "T-3", statut: "backlog", titre: "Documenter" }),
    ],
    agents: [
      agentFactice({ nom: "dev", statut: "occupe", tache_courante: "T-1" }),
      agentFactice({ nom: "qa", role: "Testeur", taches_terminees: 4 }),
    ],
    evenements: [
      evenementFactice({ statut: "en_cours" }),
      evenementFactice({ tache_id: "T-2", statut: "terminee" }),
    ],
    couts: [coutExecutionFactice({ total: usageFactice({ cout_usd: 1.42 }) })],
  };
}

/** Le run soldé — présent dans les deux états, il n'attend aucun arbitrage. */
function runSolde() {
  return runFactice({
    run_id: "run-0",
    statut: EXECUTION_TERMINEE,
    nb_taches: 3,
    cout_usd: 1.42,
    fin: "2026-07-28T10:12:00Z",
  });
}

function poserJournalDuProjet(): void {
  poserJournal([
    entreeJournalFactice({ titre: "Écrire les tests", statut: "en_cours" }),
    entreeJournalFactice({
      id: "j-0002",
      titre: "Poser le schéma",
      statut: "terminee",
    }),
  ]);
}

/**
 * Un projet qui travaille **et qui attend un arbitrage** : des tâches à
 * plusieurs statuts, deux agents, une validation en attente, deux runs — dont un
 * arrêté sur son brief — et un grand livre. Un écran vide n'a presque pas de
 * balises : l'auditer rendrait un vert qui ne parle que du vide, et c'est
 * exactement le verdict qu'on ne veut pas.
 */
export function peuplerEtat(): void {
  poserEtatGlobal({
    ...socleDuProjet(),
    validations: [validationFactice()],
    executions: [
      runFactice({ run_id: "run-1", statut: EXECUTION_EN_ATTENTE_BRIEF }),
      runSolde(),
    ],
  });
  poserJournalDuProjet();
}

/**
 * Le même projet, **files d'arbitrage vides** : rien n'attend de décision
 * humaine (#539). C'est l'état dans lequel les blocs d'arbitrage
 * (`PanneauBriefs`, `PanneauValidations`, `PanneauRunsImmobiles`) rendent `null`,
 * et c'est ce qui rend leur exemption du plafond **vérifiable** au lieu d'être
 * déclarée : un bloc qui prétend arbitrer et reste là compte comme les autres.
 *
 * ⚠ Il reste **peuplé par ailleurs**, et ce n'est pas un détail : tout vider
 * ferait basculer le tableau de bord sur `PosteVide` (#186/#281), c'est-à-dire
 * sur un écran qui n'est pas celui qu'on mesure. Une validation **déjà
 * tranchée** est conservée pour la même raison côté `/validations` : elle
 * alimente l'historique, qui est un bloc de corps, pendant que la file, elle,
 * est bien vide.
 */
export function peuplerEtatSansArbitrage(): void {
  poserEtatGlobal({
    ...socleDuProjet(),
    validations: [
      validationFactice({ tache_id: "T-2", statut: VALIDATION_APPROUVEE }),
    ],
    executions: [runSolde()],
  });
  poserJournalDuProjet();
}

// --- Les écrans du menu -----------------------------------------------------

export type Ecran = { href: string; rendu: () => React.ReactElement };

/**
 * Un écran = une entrée de menu et le composant que sa route rend.
 *
 * `/agents` est le seul à ne pas passer par son fichier `page.tsx` : c'est un
 * composant **serveur `async`** qui ne fait que lire `?onglet=` avant de rendre
 * `ListeAgents`, et un composant async ne se monte pas dans Testing Library. On
 * rend donc ce qu'il rend, avec l'onglet qu'il aurait résolu — la coquille
 * sautée ne porte pas une balise.
 */
export const ECRANS: Ecran[] = [
  { href: "/", rendu: () => <PageTableauDeBord /> },
  { href: "/chat", rendu: () => <PageChat /> },
  { href: "/runs", rendu: () => <PageRuns /> },
  {
    href: "/agents",
    rendu: () => <ListeAgents ongletCible={ongletAgentOuDefaut(undefined)} />,
  },
  { href: "/integrations", rendu: () => <PageIntegrations /> },
  { href: "/couts", rendu: () => <PageCouts /> },
  { href: "/validations", rendu: () => <PageValidations /> },
  { href: "/journal", rendu: () => <PageJournalEcran /> },
  { href: "/parametres", rendu: () => <PageParametres /> },
];

/**
 * Monte un écran dans son shell réel, attend que la garde du projet ouvre, puis
 * **laisse passer le tick de chargement différé**.
 *
 * ⚠ Cette seconde attente a manqué de #537 à #270, et son absence coûtait la
 * moitié de ce que les deux sondes prétendent mesurer. Le `h1` de la barre
 * supérieure est là **dès le premier rendu** — il vient du menu, pas des
 * données —, donc `findByRole` rendait la main avant qu'aucun écran chargeant
 * en différé n'ait reçu quoi que ce soit : `/integrations` était audité sur
 * « Chargement des intégrations… », et `/agents` sur sa propre attente. Un
 * écran vide n'a presque pas de balises, donc axe n'y trouvait rien et le
 * comptage de sobriété n'y voyait aucun bloc — un vert qui ne parle que du
 * vide, exactement ce que `peuplerEtat` existe pour éviter.
 *
 * Le tick est celui des écrans eux-mêmes (`setTimeout(…, 0)` puis la promesse
 * de `@/lib/api`), d'où une attente de la même forme plutôt qu'un `waitFor` sur
 * un texte, qui demanderait à ce harnais de connaître le contenu de chaque
 * écran.
 */
export async function monterEcran(ecran: Ecran) {
  poserChemin(ecran.href);
  render(<Shell>{ecran.rendu()}</Shell>);
  // La barre supérieure titre la page depuis le menu : sa présence dit que la
  // garde du projet a tranché et que l'écran est monté sous le cadre.
  await screen.findByRole("heading", { level: 1 });
  await act(async () => {
    await new Promise((resoudre) => setTimeout(resoudre, 0));
  });
}

/**
 * L'arbitrage d'une tâche, vu de l'interface (#572, lot 3 du parent #569).
 *
 * Pendant du versant Python (`tests/test_arbitrage_visible.py`), sur la moitié que
 * jsdom peut tenir. La panne d'origine (#568) était **muette** : pendant qu'un run
 * dormait sur trois demandes d'arbitrage, l'écran Validations affirmait « aucune
 * validation en attente » et la liste des runs affichait « En cours ». Deux
 * mensonges, une seule cause — la demande ne portait ni son run ni son projet.
 *
 * Trois volets :
 *
 * ① **Les trois attentes humaines ensemble**, sur ce que le contrat d'`execution.ts`
 *    en fait. Elles sont éprouvées par une **table**, et la table est confrontée à
 *    deux sources qui ne se recouvrent pas — les constantes `EXECUTION_EN_ATTENTE_*`
 *    du miroir de contrat (`lib/types`) et les causes de la table `ATTENTES`
 *    (`components/runs/EtatRun`). Une quatrième attente ajoutée d'un côté ou de
 *    l'autre hérite donc du filet, ou fait rougir la confrontation.
 * ② **L'ordre des questions**, qui est le contenu de la décision de #571 : le statut
 *    du run passe **avant** l'appariement par les tâches, et l'appariement reste en
 *    filet — il ne pouvait pas porter la réponse seul, la demande étant publiée avant
 *    que sa tâche n'existe.
 * ③ **L'écran Validations rend la demande sans rien changer de son côté** — la
 *    vérification que demande la note technique du ticket : c'est ce qui prouve que
 *    le chantier a réparé la **donnée** et non l'affichage.
 *
 * Aucun réseau : `useControlTower` est mocké par `tests/setup.ts`.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import PageValidations from "@/app/validations/page";
import { ATTENTES } from "@/components/runs/EtatRun";
import { attenteDepuis, attendUnHumain, runsEnAttente } from "@/lib/brief";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  ATTENTE_VALIDATION,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  causeDAttente,
  regimeDuRun,
  runsEnAttenteDeValidation,
  type CauseAttente,
} from "@/lib/execution";
import { libelleStatutExecution } from "@/lib/format";
import * as contrat from "@/lib/types";
import {
  EXECUTION_ANNULEE,
  EXECUTION_EN_ATTENTE_ARBITRAGE,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
  EXECUTION_EN_COURS,
} from "@/lib/types";

import {
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

// ------------------------------------ ① Les trois attentes, éprouvées ensemble

/**
 * Les trois attentes humaines d'un run, et ce que chacune doit produire.
 *
 * `en_attente_brief` (#320) et `en_attente_reponses` (#321) existaient ; #571 pose
 * le troisième exemplaire d'un motif qui fonctionnait déjà deux fois. La table les
 * tient ensemble parce que c'est **le** critère du lot : ce qui leur est commun doit
 * être vérifié sur les trois, faute de quoi une quatrième en sortirait en silence —
 * exactement comme l'arbitrage en est sorti.
 */
const ATTENTES_HUMAINES = [
  {
    nom: "brief",
    statut: EXECUTION_EN_ATTENTE_BRIEF,
    cause: ATTENTE_BRIEF,
    libelle: "Brief à valider",
  },
  {
    nom: "réponses",
    statut: EXECUTION_EN_ATTENTE_REPONSES,
    cause: ATTENTE_REPONSES,
    libelle: "Questions en attente",
  },
  {
    nom: "arbitrage",
    statut: EXECUTION_EN_ATTENTE_ARBITRAGE,
    cause: ATTENTE_VALIDATION,
    libelle: "Validation en attente",
  },
] as const;

/** Les statuts d'attente **déclarés par le contrat**, lus sur le miroir lui-même. */
const ATTENTES_DU_CONTRAT = Object.entries(contrat)
  .filter(([nom]) => nom.startsWith("EXECUTION_EN_ATTENTE_"))
  .map(([, valeur]) => valeur as string);

describe("les trois attentes humaines d'un run", () => {
  it("sont toutes dans la table, et la table n'en invente aucune", () => {
    // Le filet dont hérite une quatrième attente : les constantes du contrat
    // (`lib/types`, miroir de `state.py`) d'un côté, les causes que la carte de run
    // sait rendre de l'autre. Aucune des deux ne voit ce que l'autre voit — un
    // statut ajouté sans son rendu, ou un rendu sans son statut, fait rougir ici.
    expect(new Set(ATTENTES_HUMAINES.map((a) => a.statut))).toEqual(
      new Set(ATTENTES_DU_CONTRAT),
    );
    expect(new Set(ATTENTES_HUMAINES.map((a) => a.cause))).toEqual(
      new Set(Object.keys(ATTENTES) as CauseAttente[]),
    );
  });

  it.each(ATTENTES_HUMAINES)(
    "$nom : le statut seul suffit à dire que le run attend",
    ({ statut, cause }) => {
      // `false` en second argument, et c'est tout le critère de #571 : sans aucun
      // appariement validation → tâche → run, le statut répond. L'appariement
      // n'avait rien à apparier au moment exact où il aurait servi (#568).
      const run = runFactice({ statut });

      expect(causeDAttente(run, false)).toBe(cause);
      expect(regimeDuRun(run)).toBe(REGIME_SUSPENDU);
    },
  );

  it.each(ATTENTES_HUMAINES)(
    "$nom : elle est nommée, d'un seul mot des deux côtés",
    ({ statut, cause, libelle }) => {
      // Le badge de la carte et le libellé du statut brut doivent dire la même
      // chose : deux formulations pour un même run feraient chercher deux états.
      expect(libelleStatutExecution(statut)).toBe(libelle);
      expect(ATTENTES[cause].libelle).toBe(libelle);
      expect(ATTENTES[cause].phrase).toBeTruthy();
      expect(ATTENTES[cause].page).toBeTruthy();
    },
  );

  it.each(ATTENTES_HUMAINES)(
    "$nom : son ancienneté se lit au même endroit que les autres",
    ({ statut }) => {
      // Sans elle, une attente est indiscernable d'un run planté (#321). Le repli
      // sur `debut` vaut pour les trois : une trace d'un backend d'avant ce lot.
      expect(
        attenteDepuis(runFactice({ statut, attente_depuis: "2026-08-26T09:00:00Z" })),
      ).toBe("2026-08-26T09:00:00Z");
      expect(attenteDepuis(runFactice({ statut }))).toBe("2026-07-28T10:00:00Z");
    },
  );
});

// ------------------------------------------- ② L'ordre des questions décide

describe("l'ordre dans lequel une attente se lit", () => {
  it("fait passer le statut du run devant l'appariement par les tâches", () => {
    // Le run le dit lui-même : aucune liste de tâches n'est consultée, et le
    // verdict ne dépend pas de ce que le shell a chargé.
    expect(
      causeDAttente(runFactice({ statut: EXECUTION_EN_ATTENTE_ARBITRAGE }), false),
    ).toBe(ATTENTE_VALIDATION);
  });

  it("garde l'appariement en filet, pour ce qui ne porte pas son run", () => {
    // Une trace d'avant le chantier, ou un producteur qui ne porte pas son run
    // (#570) : le run reste `en_cours`, et c'est l'appariement qui répond. Le
    // retirer reperdrait ces demandes-là.
    expect(causeDAttente(runFactice(), true)).toBe(ATTENTE_VALIDATION);
    expect(regimeDuRun(runFactice(), true)).toBe(REGIME_SUSPENDU);

    const taches = [tacheFactice({ id: "T-1", run_id: "run-1" })];
    expect(
      runsEnAttenteDeValidation([validationFactice({ tache_id: "T-1" })], taches),
    ).toEqual(new Set(["run-1"]));
  });

  it("ne retient pas un run soldé, quelle que soit la demande qui traîne", () => {
    // « Soldé » passe avant tout le reste : une demande restée ouverte sur la
    // tâche d'un run annulé ne le fait pas repasser pour suspendu.
    expect(regimeDuRun(runFactice({ statut: EXECUTION_ANNULEE }), true)).not.toBe(
      REGIME_SUSPENDU,
    );
    expect(regimeDuRun(runFactice({ statut: EXECUTION_EN_COURS }))).toBe(
      REGIME_TRAVAILLE,
    );
  });

  it("laisse l'arbitrage hors des écrans du brief, et c'est délibéré", () => {
    // `lib/brief` sert le panneau du tableau de bord, la file de la cloche et
    // l'écran de validation du brief : un run arrêté sur une action sensible n'a
    // aucun brief à relire, et l'y lister enverrait chercher une décision de
    // cadrage là où c'est un arbitrage qui attend (`lib/brief`, #571).
    const arbitrage = runFactice({ statut: EXECUTION_EN_ATTENTE_ARBITRAGE });

    expect(attendUnHumain(arbitrage)).toBe(false);
    expect(attendUnHumain(runFactice({ statut: EXECUTION_EN_ATTENTE_BRIEF }))).toBe(true);
    expect(runsEnAttente([arbitrage])).toEqual([]);
    // …mais la question générale, elle, le retient : les deux lectures ne sont
    // pas la même, et c'est `causeDAttente` qui porte celle des trois attentes.
    expect(causeDAttente(arbitrage, false)).toBe(ATTENTE_VALIDATION);
  });
});

// ------------------------------ ③ L'écran rend la demande, sans rien changer

describe("l'écran Validations, une fois la donnée réparée", () => {
  it.each([
    ["une demande d'avant le chantier", ""],
    ["une demande qui porte son run (#570)", "5f531654e03b"],
  ])("rend %s à l'identique", (_nom, runId) => {
    // La vérification que demande la note technique du ticket : l'écran n'a pas
    // bougé d'une ligne pour #570/#571 — il montrait déjà tout ce qu'il faut pour
    // trancher, il ne recevait simplement rien. Un champ de plus sur la demande ne
    // change donc ni ce qu'il affiche, ni les gestes qu'il propose.
    rendreAvecEtat(
      <PageValidations />,
      { validations: [validationFactice({ run_id: runId })] },
      projetFactice(),
    );

    expect(screen.getByText("Publier la version 1.2")).toBeInTheDocument();
    expect(screen.getByText(/Agent devops/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Approuver/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Refuser/ })).toBeInTheDocument();
    expect(
      screen.queryByText(/Aucune validation en attente/),
    ).not.toBeInTheDocument();
  });

  it("distingue toujours « rien encore » de « rien en attente »", () => {
    // Le contre-test du précédent : sans demande, l'écran dit le vide qui est le
    // sien — c'est la phrase qui a menti pendant treize minutes (#568), et elle
    // ne devient juste que parce que la demande arrive désormais jusqu'ici.
    const projet = projetFactice();
    rendreAvecEtat(<PageValidations />, { validations: [] }, projet);

    expect(
      screen.getByText(new RegExp(`Rien encore sur ${projet.nom}`)),
    ).toBeInTheDocument();
  });
});

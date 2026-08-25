/**
 * Les runs perdus, côté écran (#351, lot 4/4 de #347) — la suite différée de #349.
 *
 * Deux couches, et c'est la première qui porte le sujet. `lib/execution.ts` décide
 * **ce qu'on propose de reprendre** ; le panneau ne fait que le rendre. Une erreur
 * dans la règle ne se voit nulle part : le panneau s'afficherait aussi bien, avec
 * un run de trop ou un run de moins, et le bouton du run de trop échouerait en 422
 * sans que rien n'explique pourquoi.
 *
 * L'écart à tenir est celui que le backend assume et que l'UI ne suit pas : l'API
 * **accepte** de relancer un run `indetermine` — sans quoi les quatre runs fantômes
 * du 2026-08-17, tous antérieurs au battement, seraient définitivement perdus — mais
 * l'UI ne le **propose** pas. Accepter n'est pas proposer : proposer sur une absence
 * d'information serait deviner, ce que le troisième verdict existe pour refuser.
 * Deux tests l'encadrent des deux côtés (ici, et
 * `tests/test_relance_run.py::test_un_run_qui_n_a_jamais_battu_se_relance_quand_meme`).
 *
 * Aucun réseau : `relancer` est une fonction du test, comme le hook global l'est
 * pour tous les autres écrans (`tests/setup.ts`).
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PanneauRunsPerdus } from "@/components/PanneauRunsPerdus";
import {
  estEteint,
  estOrphelin,
  estRelancable,
  runsRelancables,
} from "@/lib/execution";
import {
  CAUSE_ANNULATION,
  CAUSE_EXTINCTION,
  EXECUTION_ANNULEE,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
  VITALITE_INDETERMINE,
  VITALITE_ORPHELIN,
  VITALITE_VIVANT,
  type ResumeExecution,
} from "@/lib/types";

import { rendreAvecEtat } from "./aides";

/**
 * Un run **perdu et récupérable** : orphelin, brief approuvé. Les tests ne
 * surchargent que ce qui les intéresse — c'est le cas nominal du panneau qui sert
 * de base, les exclusions se lisant alors comme un seul champ modifié.
 */
function runPerdu(partiel: Partial<ResumeExecution> = {}): ResumeExecution {
  return {
    run_id: "3ff0bcb065f9",
    objectif: "Prototyper un mini-CRM",
    statut: EXECUTION_EN_COURS,
    vitalite: VITALITE_ORPHELIN,
    brief_approuve: true,
    reprise_de: "",
    nb_taches: 5,
    cout_usd: 2.52,
    ticket: null,
    projet_id: "prj-7f3a1c2b",
    debut: "2026-08-14T16:10:00Z",
    fin: null,
    ...partiel,
  };
}

/**
 * Un run **éteint avec Maestro** (#486) : soldé exprès, donc terminal et sans
 * verdict de vitalité — c'est sa `cause` qui le rend récupérable, et elle seule.
 *
 * Il part du run perdu ci-dessus et n'en change que ce qui compte : les tests se
 * lisent alors comme la différence entre les deux états, qui est tout le sujet.
 */
function runEteint(partiel: Partial<ResumeExecution> = {}): ResumeExecution {
  return runPerdu({
    statut: EXECUTION_ANNULEE,
    vitalite: null,
    cause: CAUSE_EXTINCTION,
    fin: "2026-08-25T09:00:00Z",
    ...partiel,
  });
}

describe("la règle — ce qu'on propose de reprendre", () => {
  it("retient un orphelin dont le brief a été approuvé", () => {
    const run = runPerdu();
    expect(estOrphelin(run)).toBe(true);
    expect(estRelancable(run)).toBe(true);
    expect(runsRelancables([run])).toEqual([run]);
  });

  it("écarte un run vivant : il n'y a rien à reprendre d'un run qui travaille", () => {
    expect(runsRelancables([runPerdu({ vitalite: VITALITE_VIVANT })])).toEqual([]);
  });

  it("écarte un run indéterminé, que l'API accepte pourtant de relancer", () => {
    // Le seul endroit du dispositif où l'UI est *plus* stricte que le backend, et
    // c'est délibéré : `indetermine` ne dit pas « mort », il dit « on ne sait pas ».
    // Le geste reste possible depuis l'API pour les fantômes antérieurs à #348 ;
    // ce qui disparaît ici, c'est la *proposition*.
    const inconnu = runPerdu({ vitalite: VITALITE_INDETERMINE });
    expect(estOrphelin(inconnu)).toBe(false);
    expect(runsRelancables([inconnu])).toEqual([]);
  });

  it("écarte un orphelin sans brief approuvé : le bouton n'aboutirait pas", () => {
    // 422 côté API — il s'est arrêté avant que quelqu'un ne valide son cadrage.
    // L'offrir ferait passer pour une panne ce qui est un run mort avant d'avoir
    // rien coûté.
    expect(runsRelancables([runPerdu({ brief_approuve: false })])).toEqual([]);
    expect(runsRelancables([runPerdu({ brief_approuve: undefined })])).toEqual([]);
  });

  it("écarte un run soldé, qui n'a plus de verdict du tout", () => {
    const solde = runPerdu({ statut: EXECUTION_TERMINEE, vitalite: null });
    expect(runsRelancables([solde])).toEqual([]);
  });

  it("retient un run que l'extinction de Maestro a soldé", () => {
    // #486 — le second état récupérable, et il ne ressemble pas au premier : ce
    // run-là n'est pas perdu, il a été soldé exprès. Son statut est donc terminal
    // et `estOrphelin` répond non, à raison — son hôte n'a pas cessé de battre,
    // on l'a éteint. C'est la **cause** qui le distingue, et rien d'autre.
    const eteint = runEteint();
    expect(estOrphelin(eteint)).toBe(false);
    expect(estEteint(eteint)).toBe(true);
    expect(runsRelancables([eteint])).toEqual([eteint]);
  });

  it("écarte un run annulé à la main, sous le même statut", () => {
    // Le statut consigné est le **même** (`annulee`) : seule la cause sépare « on a
    // éteint l'application qui tenait ce run » de « quelqu'un a arrêté ce run-là ».
    // Les confondre reproposerait de reprendre un run que son auteur venait
    // délibérément d'annuler, à chaque rechargement du tableau de bord.
    const annule = runEteint({ cause: CAUSE_ANNULATION });
    expect(estEteint(annule)).toBe(false);
    expect(runsRelancables([annule])).toEqual([]);
  });

  it("écarte un run éteint sans brief approuvé : rien de payé à rejouer", () => {
    // L'extinction ouvre la porte du statut, jamais celle du cadrage : la seconde
    // moitié de la règle vaut des deux côtés, et l'API refuserait en 422.
    expect(runsRelancables([runEteint({ brief_approuve: false })])).toEqual([]);
  });

  it("n'appelle pas « éteint » un run en vol qui porterait une cause", () => {
    // Les deux moitiés d'`estEteint` disent aujourd'hui la même chose — la
    // projection efface la cause dès qu'un run repart. Le jour où elles ne le
    // diraient plus, proposer « Reprendre » sur un run qui travaille serait la
    // pire des deux lectures.
    const en_vol = runPerdu({ vitalite: VITALITE_VIVANT, cause: CAUSE_EXTINCTION });
    expect(estEteint(en_vol)).toBe(false);
    expect(runsRelancables([en_vol])).toEqual([]);
  });

  it("garde l'ordre du backend — le plus récent d'abord, sans retrier", () => {
    // `GET /api/executions` rend déjà ses résumés récents d'abord : le run qu'on
    // vient de perdre est celui qu'on veut récupérer. Retrier ici poserait une
    // seconde règle d'ordre à tenir d'accord avec la première, pour rien.
    const recent = runPerdu({ run_id: "recent" });
    const vieux = runPerdu({ run_id: "vieux" });
    const vivant = runPerdu({ run_id: "vivant", vitalite: VITALITE_VIVANT });

    expect(runsRelancables([recent, vivant, vieux]).map((r) => r.run_id)).toEqual([
      "recent",
      "vieux",
    ]);
  });
});

describe("le panneau — ce qu'on voit et ce qu'on déclenche", () => {
  it("ne s'affiche pas du tout quand rien n'est récupérable", () => {
    // Pas un panneau vide : un encart « aucun run perdu » sur un tableau de bord
    // sain occuperait la place de ce qui attend vraiment quelqu'un.
    rendreAvecEtat(
      <PanneauRunsPerdus
        executions={[runPerdu({ vitalite: VITALITE_VIVANT })]}
        relancer={vi.fn()}
      />,
    );
    expect(screen.queryByRole("region", { name: "Runs interrompus" })).toBeNull();
  });

  it("annonce combien de runs sont perdus et propose de les reprendre", () => {
    rendreAvecEtat(
      <PanneauRunsPerdus
        executions={[runPerdu(), runPerdu({ run_id: "4b33ea332e60" })]}
        relancer={vi.fn()}
      />,
    );

    const panneau = screen.getByRole("region", { name: "Runs interrompus" });
    expect(within(panneau).getAllByRole("button", { name: "Reprendre" })).toHaveLength(
      2,
    );
    // L'identifiant est affiché à côté de l'objectif : c'est lui qu'on retrouve
    // dans le journal quand on veut savoir ce que le run avait déjà fait.
    expect(panneau.textContent).toContain("3ff0bcb065f9");
    expect(panneau.textContent).toContain("Prototyper un mini-CRM");
  });

  it("dit d'où vient chaque run, sous le même bouton", async () => {
    // #486 — les deux états mènent au **même** geste (c'est le critère du ticket :
    // « par le bouton existant »), et c'est justifié : ce que la relance rejoue est
    // un cadrage, qu'on l'ait perdu ou rangé. Seule l'origine se dit, parce que
    // présenter une extinction volontaire comme une panne ferait chercher un
    // incident après un simple redémarrage.
    rendreAvecEtat(
      <PanneauRunsPerdus
        executions={[runPerdu(), runEteint({ run_id: "4b33ea332e60" })]}
        relancer={vi.fn()}
      />,
    );

    const panneau = screen.getByRole("region", { name: "Runs interrompus" });
    expect(within(panneau).getAllByRole("button", { name: "Reprendre" })).toHaveLength(
      2,
    );
    expect(panneau.textContent).toContain("hôte muet");
    expect(panneau.textContent).toContain("arrêté avec Maestro");
  });

  it("reprend le run sur lequel on a cliqué, et lui seul", async () => {
    const relancer = vi.fn().mockResolvedValue(runPerdu({ run_id: "suite" }));
    rendreAvecEtat(
      <PanneauRunsPerdus
        executions={[runPerdu(), runPerdu({ run_id: "4b33ea332e60" })]}
        relancer={relancer}
      />,
    );

    const panneau = screen.getByRole("region", { name: "Runs interrompus" });
    const [, second] = within(panneau).getAllByRole("button", { name: "Reprendre" });
    await userEvent.click(second);

    await waitFor(() => expect(relancer).toHaveBeenCalledTimes(1));
    expect(relancer).toHaveBeenCalledWith("4b33ea332e60");
  });

  it("désarme le bouton pendant la reprise : jamais deux relances pour un clic de trop", async () => {
    // Un double clic partirait deux fois. L'API refuserait la seconde (409, le run
    // venant d'être soldé), mais le message de refus s'afficherait sur une carte
    // dont la reprise a *réussi* — un échec annoncé là où tout s'est bien passé.
    let terminer: (r: ResumeExecution) => void = () => {};
    const relancer = vi.fn(
      () => new Promise<ResumeExecution>((resoudre) => (terminer = resoudre)),
    );
    rendreAvecEtat(
      <PanneauRunsPerdus executions={[runPerdu()]} relancer={relancer} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Reprendre" }));

    const enCours = await screen.findByRole("button", { name: "Reprise…" });
    expect(enCours).toBeDisabled();

    terminer(runPerdu({ run_id: "suite" }));
    await waitFor(() => expect(relancer).toHaveBeenCalledTimes(1));
  });

  it("affiche le refus de l'API et réarme le bouton", async () => {
    // C'est le message du backend qui s'affiche, pas une phrase inventée ici : lui
    // seul sait ce qu'il a refusé (déjà soldé, encore vivant, sans cadrage).
    const relancer = vi
      .fn()
      .mockRejectedValue(new Error("exécution encore vivante : son hôte bat toujours."));
    rendreAvecEtat(
      <PanneauRunsPerdus executions={[runPerdu()]} relancer={relancer} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Reprendre" }));

    expect(await screen.findByText(/encore vivante/)).toBeInTheDocument();
    // Réarmé : le refus peut être transitoire (un run vivant finit par se taire),
    // et laisser le bouton mort obligerait à recharger la page pour réessayer.
    expect(screen.getByRole("button", { name: "Reprendre" })).toBeEnabled();
  });

  it("ne dit rien de plus quand la reprise réussit — la carte est là pour partir", async () => {
    // Aucun message de succès, et le bouton **reste** désarmé : la relance solde ce
    // run, donc le rechargement le fait sortir de la liste. Une carte qui disparaît
    // dit déjà ce qui s'est passé, et un « repris ✓ » sur un composant qu'on démonte
    // aussitôt ne serait jamais lu. Le réarmer serait pire que superflu : il
    // proposerait de reprendre un run qu'on vient de solder, le temps d'un
    // rechargement.
    const relancer = vi.fn().mockResolvedValue(runPerdu({ run_id: "suite" }));
    rendreAvecEtat(
      <PanneauRunsPerdus executions={[runPerdu()]} relancer={relancer} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Reprendre" }));
    await waitFor(() => expect(relancer).toHaveBeenCalled());

    const panneau = screen.getByRole("region", { name: "Runs interrompus" });
    expect(within(panneau).getByRole("button", { name: "Reprise…" })).toBeDisabled();
    expect(within(panneau).queryByRole("button", { name: "Reprendre" })).toBeNull();
  });
});

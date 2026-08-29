/**
 * Les runs perdus, côté écran (#351, lot 4/4 de #347) — la suite différée de #349.
 *
 * ⚠ Le panneau qu'elle monte s'appelle `PanneauRunsImmobiles` depuis #738, qui lui
 * a confié le **second** verdict de surveillance : les runs qu'on a laissés
 * attendre y sont rangés à côté des runs perdus, sous deux familles nommées et
 * deux gestes distincts. Le fichier a suivi le nom du composant, parce qu'un
 * fichier qui garde `PanneauRunsPerdus` enverrait chercher un composant qui
 * n'existe plus.
 *
 * Il couvre donc **les deux familles** depuis #739, en trois temps : la règle des
 * runs perdus et leur panneau (#351), la règle du second verdict (#738), puis ce
 * que sa carte rend (#739, dernier bloc). Le versant backend du second verdict est
 * dans `tests/test_souffrance.py`, et le partage est celui que ce fichier tient
 * déjà pour le premier — le seuil, ses écarts et le sens du verdict vivent dans
 * `maestro/controltower/souffrance.py`, jamais recopiés ici.
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

import {
  PanneauRunsImmobiles,
  TITRE_RUNS_IMMOBILES,
} from "@/components/PanneauRunsImmobiles";
import { ATTENTES } from "@/components/runs/EtatRun";
import {
  causeDAttente,
  estEnSouffrance,
  estEteint,
  estOrphelin,
  estRelancable,
  runsEnSouffrance,
  runsRelancables,
} from "@/lib/execution";
import {
  CAUSE_ANNULATION,
  CAUSE_EXTINCTION,
  EXECUTION_ANNULEE,
  EXECUTION_EN_ATTENTE_ARBITRAGE,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
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
      <PanneauRunsImmobiles
        executions={[runPerdu({ vitalite: VITALITE_VIVANT })]}
        relancer={vi.fn()}
      />,
    );
    expect(screen.queryByRole("region", { name: TITRE_RUNS_IMMOBILES })).toBeNull();
  });

  it("annonce combien de runs sont perdus et propose de les reprendre", () => {
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runPerdu(), runPerdu({ run_id: "4b33ea332e60" })]}
        relancer={vi.fn()}
      />,
    );

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
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
      <PanneauRunsImmobiles
        executions={[runPerdu(), runEteint({ run_id: "4b33ea332e60" })]}
        relancer={vi.fn()}
      />,
    );

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
    expect(within(panneau).getAllByRole("button", { name: "Reprendre" })).toHaveLength(
      2,
    );
    expect(panneau.textContent).toContain("hôte muet");
    expect(panneau.textContent).toContain("arrêté avec Maestro");
  });

  it("reprend le run sur lequel on a cliqué, et lui seul", async () => {
    const relancer = vi.fn().mockResolvedValue(runPerdu({ run_id: "suite" }));
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runPerdu(), runPerdu({ run_id: "4b33ea332e60" })]}
        relancer={relancer}
      />,
    );

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
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
      <PanneauRunsImmobiles executions={[runPerdu()]} relancer={relancer} />,
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
      <PanneauRunsImmobiles executions={[runPerdu()]} relancer={relancer} />,
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
      <PanneauRunsImmobiles executions={[runPerdu()]} relancer={relancer} />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Reprendre" }));
    await waitFor(() => expect(relancer).toHaveBeenCalled());

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
    expect(within(panneau).getByRole("button", { name: "Reprise…" })).toBeDisabled();
    expect(within(panneau).queryByRole("button", { name: "Reprendre" })).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Le second verdict (#738) — la règle de tri
 * ------------------------------------------------------------------ */

/**
 * Un run **qu'on a laissé attendre** : arrêté sur son brief, vivant, et au-delà du
 * seuil. Le champ vient du backend (#737) et n'est jamais recalculé ici — c'est la
 * moitié de la règle que ces tests gardent.
 */
function runEnSouffrance(partiel: Partial<ResumeExecution> = {}): ResumeExecution {
  return runPerdu({
    run_id: "a1b2c3d4e5f6",
    objectif: "Cadrer la reprise du CRM",
    statut: EXECUTION_EN_ATTENTE_BRIEF,
    vitalite: VITALITE_VIVANT,
    en_souffrance: true,
    attente_depuis: "2026-08-14T16:10:00Z",
    ...partiel,
  });
}

/**
 * Ce que #738 a gardé **le jour même** : la règle de tri et la propriété
 * **négative** du critère 2 — pas de carte oui/non. Une propriété négative qu'on
 * n'écrit pas au moment où elle est vraie est une propriété que le prochain lot
 * ajoutera sans s'en apercevoir.
 */
describe("la règle — ce qu'on signale comme laissé en attente", () => {
  it("retient un run vivant que personne n'a fait avancer", () => {
    const run = runEnSouffrance();
    expect(estEnSouffrance(run)).toBe(true);
    expect(runsEnSouffrance([run])).toEqual([run]);
    // Et il n'entre pas dans l'autre famille : les deux verdicts ne désignent pas
    // les mêmes runs, c'est tout le sujet du panneau.
    expect(runsRelancables([run])).toEqual([]);
  });

  it("ne déduit rien d'un `attente_depuis` ancien : le verdict est celui du backend", () => {
    // Le seuil, ses écarts et le sens de chaque verdict vivent dans
    // `maestro/controltower/souffrance.py`. Une formule recopiée ici se périmerait
    // à la première correction, et `docs/33 §5.4` dit d'avance que ce chiffre bougera.
    const sansVerdict = runEnSouffrance({ en_souffrance: undefined });
    expect(estEnSouffrance(sansVerdict)).toBe(false);
    expect(runsEnSouffrance([sansVerdict])).toEqual([]);
  });

  it("écarte un orphelin, qu'il faut reprendre et non aller voir", () => {
    // Il attend bien quelqu'un — mais son hôte est mort, donc personne ne recevrait
    // la réponse. C'est le deuxième cran de `regimeDuRun` (#474), et il range ce
    // run dans l'autre famille, sous le bouton qui le rejoue.
    const mort = runEnSouffrance({ vitalite: VITALITE_ORPHELIN });
    expect(estEnSouffrance(mort)).toBe(true);
    expect(runsEnSouffrance([mort])).toEqual([]);
    expect(runsRelancables([mort])).toEqual([mort]);
  });

  it("écarte un run en pause, où quelqu'un a déjà décidé", () => {
    // Le backend l'assume comme un faux positif (la pause est un drapeau à côté du
    // statut, pas dedans) ; l'écran, lui, a déjà tranché que la pause l'emporte sur
    // l'attente. Alerter dessus reviendrait à alerter sur l'exercice d'une commande
    // qu'on offre (docs/33 §3.2).
    expect(runsEnSouffrance([runEnSouffrance({ en_pause: true })])).toEqual([]);
  });

  it("écarte un run soldé, qui n'attend plus personne", () => {
    const solde = runEnSouffrance({ statut: EXECUTION_TERMINEE, vitalite: null });
    expect(runsEnSouffrance([solde])).toEqual([]);
  });
});

describe("le panneau — deux familles, deux gestes", () => {
  it("range les deux verdicts à part et compte l'ensemble", () => {
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runEnSouffrance(), runPerdu()]}
        relancer={vi.fn()}
      />,
    );

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
    const attente = within(panneau).getByRole("list", {
      name: "Personne n'a répondu",
    });
    const perdus = within(panneau).getByRole("list", {
      name: "Leur hôte s'est tu",
    });
    expect(within(attente).getAllByRole("listitem")).toHaveLength(1);
    expect(within(perdus).getAllByRole("listitem")).toHaveLength(1);
    // Ce que le run attend est nommé, et son ancienneté vient **après** : le tri
    // fait le signal, l'horodatage ne dit que de combien.
    expect(attente.textContent).toContain("Brief à valider");
  });

  it("ne propose aucun oui/non sur un run laissé en attente, mais un renvoi vers lui", () => {
    // Le critère 2 du ticket, et la règle de #647 appliquée une troisième fois : la
    // réponse à une attente n'est ni oui ni non (« répondre », « relever le budget »,
    // « annuler », « rien »), donc il n'y a pas de geste à mettre sous une carte.
    rendreAvecEtat(
      <PanneauRunsImmobiles executions={[runEnSouffrance()]} relancer={vi.fn()} />,
    );

    const panneau = screen.getByRole("region", { name: TITRE_RUNS_IMMOBILES });
    expect(within(panneau).queryAllByRole("button")).toEqual([]);
    expect(
      within(panneau).getByRole("link", { name: "Aller voir" }),
    ).toHaveAttribute("href", "/runs/a1b2c3d4e5f6");
  });

  it("laisse le run en souffrance devant le run perdu", () => {
    // L'arbitrage de #349, déjà rendu un cran plus haut par l'ordre des panneaux :
    // ce qui retient du travail **vivant** passe devant ce qui ne retient plus rien.
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runPerdu(), runEnSouffrance()]}
        relancer={vi.fn()}
      />,
    );

    const titres = screen
      .getAllByRole("heading", { level: 3 })
      .map((titre) => titre.textContent ?? "");
    expect(titres[0]).toContain("Personne n'a répondu");
    expect(titres[1]).toContain("Leur hôte s'est tu");
  });
});

/* ------------------------------------------------------------------ *
 * La part différée à #739 — ce que la carte d'un run laissé en attente dit
 * ------------------------------------------------------------------ */

/**
 * Le pendant, côté écran, de `tests/test_souffrance.py`.
 *
 * #738 avait gardé la **règle** de tri et la propriété négative du critère 2 ; ce
 * qui restait est ce que la carte *rend* — et c'est là que se joue le reproche
 * d'origine du chantier. Huit endroits affichaient déjà `attente_depuis` sans que
 * personne sache **ce que** le run attendait ni **si** l'attente était anormale :
 * une carte qui dirait « en attente · il y a 3 h » sans nommer le geste manquant
 * reproduirait cet écran-là dans un panneau d'alerte, ce qui serait pire.
 */
describe("la carte — ce que le run attend, et depuis quand", () => {
  it.each([
    [EXECUTION_EN_ATTENTE_BRIEF, "Brief à valider"],
    [EXECUTION_EN_ATTENTE_REPONSES, "Questions en attente"],
    [EXECUTION_EN_ATTENTE_ARBITRAGE, "Validation en attente"],
  ])("nomme ce que le run attend — %s", (statut, libelle) => {
    // Le libellé n'est pas réécrit dans le panneau : il vient de la table
    // `ATTENTES` d'`EtatRun`, sous laquelle le badge, la ligne d'attente et la
    // liste des runs nomment déjà la même chose. Ce test lit donc la table plutôt
    // que la chaîne, sinon il figerait ici un vocabulaire qui vit ailleurs — et
    // deux formulations du même état finiraient par diverger.
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runEnSouffrance({ statut })]}
        relancer={vi.fn()}
      />,
    );

    const attente = screen.getByRole("list", { name: "Personne n'a répondu" });
    expect(attente.textContent).toContain(libelle);
    expect(Object.values(ATTENTES).map((a) => a.libelle)).toContain(libelle);
  });

  it("couvre les trois attentes du backend, et pas une de moins", () => {
    // Le filet dont hérite une **quatrième** attente : le backend en tient la
    // liste (`STATUTS_EXECUTION_EN_ATTENTE`, éprouvée par
    // `tests/test_souffrance.py`), et `causeDAttente` doit savoir nommer chacune.
    // Sans cette confrontation, une attente nouvelle tomberait sur le repli
    // « en attente » — un run signalé sans qu'on sache ce qu'il réclame.
    const statuts = [
      EXECUTION_EN_ATTENTE_BRIEF,
      EXECUTION_EN_ATTENTE_REPONSES,
      EXECUTION_EN_ATTENTE_ARBITRAGE,
    ];
    for (const statut of statuts) {
      expect(causeDAttente(runEnSouffrance({ statut }), false)).not.toBeNull();
    }
  });

  it("dit l'ancienneté à l'œil comme à l'oreille, jamais par le seul glyphe", () => {
    // Le chrono est une icône, donc muette pour qui écoute : la carte porte le
    // mot « attend depuis » en `sr-only`. Sans lui, la ligne se lirait « Cadrer la
    // reprise du CRM · a1b2… · Brief à valider · il y a 15 j », où la dernière
    // valeur n'est rattachée à rien.
    rendreAvecEtat(
      <PanneauRunsImmobiles executions={[runEnSouffrance()]} relancer={vi.fn()} />,
    );

    expect(
      screen.getByRole("list", { name: "Personne n'a répondu" }).textContent,
    ).toContain("attend depuis");
  });

  it("ne montre aucune ancienneté quand le backend n'en donne pas", () => {
    // Le cas qu'`en_souffrance` traite en signalant quand même (un horodatage
    // illisible rend `true`, cf. `souffrance.py`) : le run **doit** rester
    // affiché, et c'est seulement le « depuis quand » qui manque. Inventer un
    // repli — « depuis longtemps », ou l'heure de début — dirait un fait qu'on n'a
    // pas, sur la carte même qui existe pour ne plus rien affirmer de faux.
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runEnSouffrance({ attente_depuis: null })]}
        relancer={vi.fn()}
      />,
    );

    const attente = screen.getByRole("list", { name: "Personne n'a répondu" });
    expect(within(attente).getAllByRole("listitem")).toHaveLength(1);
    expect(attente.textContent).toContain("Brief à valider");
    expect(attente.textContent).not.toContain("attend depuis");
  });

  it("ne compte par famille que lorsqu'il y en a deux", () => {
    // Seule, une famille répète au mot près le compte de l'en-tête, et deux fois
    // le même nombre à deux lignes d'écart se lit comme deux chiffres. Le compte
    // par famille n'a de sens qu'**en face de l'autre**.
    rendreAvecEtat(
      <PanneauRunsImmobiles
        executions={[runEnSouffrance(), runEnSouffrance({ run_id: "b2c3d4e5f6a1" })]}
        relancer={vi.fn()}
      />,
    );

    const [chapeau, famille] = screen
      .getAllByRole("heading")
      .map((titre) => titre.textContent ?? "");
    expect(chapeau).toContain("2");
    expect(famille).toBe("Personne n'a répondu");
  });
});

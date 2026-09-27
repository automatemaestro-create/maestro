/**
 * **Le run a atteint son budget** — la carte du fil qui pose la question, et ce
 * que ses trois gestes envoient (#1182).
 *
 * Ce que cette suite garde, dans l'ordre du second critère du ticket — « le fil
 * pose la question avec la dépense, le reste à faire et son coût estimé ;
 * « relever », « réduire » ou « arrêter » y reprennent ou soldent le run » :
 *
 * ① **la question porte ses chiffres** : la dépense lue contre le plafond, le
 *    reste à faire nommé tâche par tâche (la tâche coupée dite « mise de côté »),
 *    et le coût du reste en fourchette — **l'estimation du brief**, jamais une
 *    autre (`lib/estimation`) ;
 * ② **chaque geste envoie la décision qu'il annonce** : relever porte le montant
 *    que le bouton affiche, réduire porte les tâches décochées, arrêter ne porte
 *    rien d'autre — et **rien ne relève le plafond sans qu'un montant soit lu** ;
 * ③ **la carte est montée au pied du fil de l'orchestration**, et seulement là.
 *
 * Ni réseau ni backend : la carte reçoit un run tel que l'API le sert
 * (`ResumeExecution.plafond`), et le geste est un `vi.fn()`. Ce que le backend
 * fait de la décision est éprouvé côté API (`tests/test_plafond_control_tower.py`).
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { PlafondDansLeFil } from "@/components/chat/PlafondDansLeFil";
import { LigneAttente } from "@/components/runs/EtatRun";
import {
  COUT_TACHE_USD_BAS,
  COUT_TACHE_USD_HAUT,
  MARGE_RELANCES,
  estimerReste,
} from "@/lib/estimation";
import { ATTENTE_PLAFOND, causeDAttente, regimeDuRun, REGIME_SUSPENDU } from "@/lib/execution";
import {
  formatCout,
  formatFourchetteCout,
  formatTokens,
  libelleStatutExecution,
} from "@/lib/format";
import {
  decisionDeReprise,
  plafondPropose,
  runsAuPlafond,
  unitesFranchies,
  type RunAuPlafond,
} from "@/lib/plafond";
import {
  EXECUTION_EN_ATTENTE_PLAFOND,
  EXECUTION_EN_COURS,
  GESTE_ARRETER,
  GESTE_REDUIRE,
  GESTE_RELEVER,
  type DemandePlafond,
} from "@/lib/types";

import {
  poserChemin,
  poserFilAssistance,
  questionFactice,
  rendreAvecEtat,
  runFactice,
} from "./aides";
import { auditerLaPage, bloquantes, raconter } from "./axe";

/** La question telle que le vrai run du 2026-09-27 l'a posée (0,22 $ sur 0,01 $). */
function demandeFactice(partiel: Partial<DemandePlafond> = {}): DemandePlafond {
  return {
    run_id: "run-plafond",
    projet_id: "prj-7f3a1c2b",
    objectif: "Écrire NOTES.md puis CHANGELOG.md",
    depense_usd: 0.2213166,
    depense_tokens: 24_373,
    plafond_cout_usd: 0.01,
    plafond_tokens: null,
    raison: "plafond de dépense dépassé : 0.2213 $ consommés sur l'exécution",
    restantes: [
      { tache_id: "ecrire-notes", titre: "Écrire NOTES.md", interrompue: true },
      { tache_id: "ecrire-changelog", titre: "Écrire CHANGELOG.md", interrompue: false },
      { tache_id: "verifier", titre: "Vérifier les deux fichiers", interrompue: false },
    ],
    ...partiel,
  };
}

function runAuPlafond(partiel: Partial<DemandePlafond> = {}): RunAuPlafond {
  return {
    ...runFactice({
      run_id: "run-plafond",
      titre: "Écrire NOTES.md puis CHANGELOG.md",
      statut: EXECUTION_EN_ATTENTE_PLAFOND,
      attente_depuis: "2026-09-27T13:56:00Z",
      projet_id: "prj-7f3a1c2b",
    }),
    plafond: demandeFactice(partiel),
  };
}

function monter(run: RunAuPlafond = runAuPlafond()) {
  const trancher = vi.fn(async () => {});
  render(<PlafondDansLeFil run={run} trancher={trancher} />);
  const carte = screen.getByRole("region", { name: "Budget du run atteint" });
  return { carte, trancher };
}

/** Le texte d'une tuile de chiffre : son libellé, sa valeur, son détail. */
function tuile(carte: HTMLElement, libelle: string): string {
  const titre = within(carte).getByText(libelle);
  return titre.closest("[data-chiffre]")?.textContent ?? "";
}

// ===========================================================================
// ① La question porte ses chiffres
// ===========================================================================

describe("la question au plafond", () => {
  it("dit la dépense face au plafond, le reste à faire et son coût estimé", () => {
    const { carte } = monter();

    expect(
      within(carte).getByText(
        "Le run a atteint son plafond de dépense. Relever, réduire ou arrêter ?",
      ),
    ).toBeInTheDocument();
    expect(tuile(carte, "Dépensé")).toContain(formatCout(0.2213166));
    expect(tuile(carte, "Dépensé")).toContain(`sur un plafond de ${formatCout(0.01)}`);
    expect(tuile(carte, "Reste à faire")).toContain("3 tâches");
    // La tâche coupée se compte dans le reste, et se dit — jamais « tout le plan ».
    expect(tuile(carte, "Reste à faire")).toContain("dont 1 mise de côté");
    const reste = estimerReste(3);
    expect(tuile(carte, "Coût estimé du reste")).toContain(
      formatFourchetteCout(reste.bas, reste.haut),
    );
    expect(tuile(carte, "Coût estimé du reste")).toContain(
      "ordre de grandeur, pas une mesure",
    );
  });

  it("nomme chaque tâche qui reste, et celle qui a été coupée comme mise de côté", () => {
    const { carte } = monter();

    for (const titre of ["Écrire NOTES.md", "Écrire CHANGELOG.md", "Vérifier les deux fichiers"]) {
      expect(within(carte).getByRole("checkbox", { name: new RegExp(titre) })).toBeChecked();
    }
    const coupee = within(carte).getByRole("checkbox", { name: /Écrire NOTES\.md/ });
    expect(coupee.closest("label")?.textContent).toContain(
      "mise de côté — travail conservé",
    );
    expect(
      within(carte)
        .getByRole("checkbox", { name: /CHANGELOG/ })
        .closest("label")?.textContent,
    ).not.toContain("mise de côté");
  });

  it("dit ce qui se passe sans réponse, et que le franchissement a pu dépasser un peu", () => {
    const { carte } = monter();

    expect(carte.textContent).toContain(
      "Sans réponse, le run reste suspendu : rien ne se dépense.",
    );
    expect(carte.textContent).toContain("a pu dépasser un peu le plafond");
  });

  it("se lit aussi depuis son pied : le plafond franchi, la dépense et le haut de l'estimation", () => {
    // Relecture de #1182 : dans la colonne de 320 px, le fil colle à son bas et
    // la carte s'ouvre sur ses gestes — titre et tuiles sont au-dessus, hors de
    // vue. Ce qu'on lit là doit suffire à comprendre ce que le bouton engage.
    const { carte } = monter();
    const champ = within(carte).getByLabelText("Nouveau plafond, en $US");
    const aide = document.getElementById(champ.getAttribute("aria-describedby") ?? "");

    expect(aide?.textContent).toContain(`${formatCout(0.2213166)} dépensés`);
    expect(aide?.textContent).toContain(
      `plus ${formatCout(estimerReste(3).haut)}, le haut de l'estimation pour 3 tâches`,
    );
    expect(carte.textContent).toContain(
      `Le plafond de ${formatCout(0.01)} est atteint. Sans réponse`,
    );
  });

  it("reprend l'estimation du brief, sans en inventer une autre", () => {
    // Les mêmes bornes par tâche et la même marge de relance que `estimerSuite` :
    // seule la décomposition, déjà payée, n'y est plus.
    expect(estimerReste(2)).toEqual({
      nbTaches: 2,
      bas: 2 * COUT_TACHE_USD_BAS,
      haut: 2 * COUT_TACHE_USD_HAUT * MARGE_RELANCES,
    });
  });
});

// ===========================================================================
// ② Chaque geste envoie la décision qu'il annonce
// ===========================================================================

describe("les trois gestes", () => {
  it("relever propose la dépense plus le haut de l'estimation, et l'envoie tel qu'affiché", async () => {
    const { carte, trancher } = monter();
    const propose = Math.ceil((0.2213166 + estimerReste(3).haut) * 100) / 100;

    expect(within(carte).getByLabelText("Nouveau plafond, en $US")).toHaveValue(
      // Écrit à la française — la virgule décimale de l'écran.
      String(propose).replace(".", ","),
    );
    await userEvent.click(
      within(carte).getByRole("button", {
        name: `Relever à ${formatCout(propose)} et reprendre`,
      }),
    );

    await waitFor(() =>
      expect(trancher).toHaveBeenCalledWith("run-plafond", {
        geste: GESTE_RELEVER,
        plafond_cout_usd: propose,
        plafond_tokens: null,
      }),
    );
  });

  it("réduire écarte ce qui est décoché, recalcule l'estimation et le plafond proposé", async () => {
    const { carte, trancher } = monter();

    await userEvent.click(within(carte).getByRole("checkbox", { name: /CHANGELOG/ }));

    const reste = estimerReste(2);
    const propose = Math.ceil((0.2213166 + reste.haut) * 100) / 100;
    expect(tuile(carte, "Reste à faire")).toContain("2 tâches");
    expect(tuile(carte, "Reste à faire")).toContain("1 tâche écartée");
    expect(tuile(carte, "Coût estimé du reste")).toContain(
      formatFourchetteCout(reste.bas, reste.haut),
    );
    await userEvent.click(
      within(carte).getByRole("button", {
        name: `Reprendre sans 1 tâche — plafond ${formatCout(propose)}`,
      }),
    );

    await waitFor(() =>
      expect(trancher).toHaveBeenCalledWith("run-plafond", {
        geste: GESTE_REDUIRE,
        plafond_cout_usd: propose,
        plafond_tokens: null,
        ecartees: ["ecrire-changelog"],
      }),
    );
  });

  it("un montant écrit par la personne tient, et le bouton le nomme", async () => {
    const { carte, trancher } = monter();
    const champ = within(carte).getByLabelText("Nouveau plafond, en $US");

    await userEvent.clear(champ);
    await userEvent.type(champ, "2,5");
    // Décocher ensuite ne réécrit pas ce que la personne a choisi.
    await userEvent.click(within(carte).getByRole("checkbox", { name: /Vérifier/ }));
    await userEvent.click(
      within(carte).getByRole("button", {
        name: `Reprendre sans 1 tâche — plafond ${formatCout(2.5)}`,
      }),
    );

    await waitFor(() =>
      expect(trancher).toHaveBeenCalledWith(
        "run-plafond",
        expect.objectContaining({ geste: GESTE_REDUIRE, plafond_cout_usd: 2.5 }),
      ),
    );
  });

  it("un plafond qui ne couvre pas ce qui est dépensé ne part pas", async () => {
    const { carte, trancher } = monter();
    const champ = within(carte).getByLabelText("Nouveau plafond, en $US");

    await userEvent.clear(champ);
    await userEvent.type(champ, "0,1");

    expect(carte.textContent).toContain("déjà dépensés");
    const bouton = within(carte).getByRole("button", { name: /Relever à/ });
    expect(bouton).toBeDisabled();
    await userEvent.click(bouton);
    expect(trancher).not.toHaveBeenCalled();
  });

  it("arrêter n'envoie que le geste", async () => {
    const { carte, trancher } = monter();

    await userEvent.click(within(carte).getByRole("button", { name: "Arrêter le run" }));

    await waitFor(() =>
      expect(trancher).toHaveBeenCalledWith("run-plafond", { geste: GESTE_ARRETER }),
    );
  });

  it("tout décocher ne laisse que l'arrêt", async () => {
    const { carte } = monter();

    for (const caseACocher of within(carte).getAllByRole("checkbox")) {
      await userEvent.click(caseACocher);
    }

    expect(within(carte).queryByRole("button", { name: /Reprendre|Relever/ })).toBeNull();
    expect(carte.textContent).toContain("le run ne peut que s'arrêter");
    expect(within(carte).getByRole("button", { name: "Arrêter le run" })).toBeEnabled();
  });

  it("au plafond en tokens, rien n'est proposé : le plafond s'écrit", async () => {
    const run = runAuPlafond({
      depense_usd: null,
      plafond_cout_usd: null,
      depense_tokens: 38_053,
      plafond_tokens: 1,
    });
    const { carte, trancher } = monter(run);
    const champ = within(carte).getByLabelText("Nouveau plafond, en tokens");

    expect(tuile(carte, "Dépensé")).toContain(`${formatTokens(38_053)} tokens`);
    expect(champ).toHaveValue("");
    expect(within(carte).getByRole("button", { name: /Relever à/ })).toBeDisabled();

    await userEvent.type(champ, "60000");
    await userEvent.click(
      within(carte).getByRole("button", {
        name: `Relever à ${formatTokens(60_000)} tokens et reprendre`,
      }),
    );
    await waitFor(() =>
      expect(trancher).toHaveBeenCalledWith("run-plafond", {
        geste: GESTE_RELEVER,
        plafond_cout_usd: null,
        plafond_tokens: 60_000,
      }),
    );
  });

  it("un refus de l'API se lit sur la carte, et la main est rendue", async () => {
    const trancher = vi.fn(async () => {
      throw new Error("cette exécution n'attend pas de décision au plafond de dépense");
    });
    render(<PlafondDansLeFil run={runAuPlafond()} trancher={trancher} />);
    const carte = screen.getByRole("region", { name: "Budget du run atteint" });

    await userEvent.click(within(carte).getByRole("button", { name: "Arrêter le run" }));

    expect(await within(carte).findByRole("alert")).toHaveTextContent(
      "n'attend pas de décision au plafond",
    );
    expect(within(carte).getByRole("button", { name: "Arrêter le run" })).toBeEnabled();
  });
});

// ===========================================================================
// Les règles partagées (`lib/plafond`)
// ===========================================================================

describe("les règles du plafond", () => {
  it("relève le plafond que la dépense atteint — les deux quand elle atteint les deux", () => {
    expect(unitesFranchies(demandeFactice())).toEqual(["usd"]);
    expect(
      unitesFranchies(demandeFactice({ depense_usd: null, plafond_cout_usd: null, plafond_tokens: 10 })),
    ).toEqual(["tokens"]);
    expect(unitesFranchies(demandeFactice({ plafond_tokens: 10 }))).toEqual(["usd", "tokens"]);
  });

  it("ne propose un plafond qu'en dollars, arrondi au centime supérieur", () => {
    expect(plafondPropose(demandeFactice(), "usd", 1)).toBe(
      Math.ceil((0.2213166 + estimerReste(1).haut) * 100) / 100,
    );
    expect(plafondPropose(demandeFactice(), "tokens", 1)).toBeNull();
  });

  it("n'écarte que des tâches que la question nommait", () => {
    expect(
      decisionDeReprise(demandeFactice(), new Set(["ecrire-notes", "verifier", "inventee"]), {
        usd: 3,
      }),
    ).toEqual({
      geste: GESTE_REDUIRE,
      plafond_cout_usd: 3,
      plafond_tokens: null,
      ecartees: ["ecrire-changelog"],
    });
  });

  it("range les runs au plafond, le plus ancien d'abord, et seulement ceux-là", () => {
    const recent = { ...runAuPlafond(), run_id: "b", attente_depuis: "2026-09-27T14:00:00Z" };
    const ancien = { ...runAuPlafond(), run_id: "a", attente_depuis: "2026-09-27T13:00:00Z" };
    const enCours = runFactice({ run_id: "c", statut: EXECUTION_EN_COURS });

    expect(runsAuPlafond([recent, enCours, ancien]).map((r) => r.run_id)).toEqual(["a", "b"]);
  });

  it("un run au plafond attend quelqu'un, et le dit avec les mots de l'écran", () => {
    const run = runAuPlafond();

    expect(causeDAttente(run, false)).toBe(ATTENTE_PLAFOND);
    expect(regimeDuRun(run)).toBe(REGIME_SUSPENDU);
    expect(libelleStatutExecution(EXECUTION_EN_ATTENTE_PLAFOND)).toBe("Budget atteint");
    rendreAvecEtat(<LigneAttente run={run} attente={ATTENTE_PLAFOND} />);
    expect(screen.getByText(/Le run attend une décision sur son budget/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Décider/ })).toHaveAttribute("href", "/chat");
  });
});

// ===========================================================================
// ③ Montée au pied du fil de l'orchestration
// ===========================================================================

describe("au pied du fil", () => {
  it("se pose sous les questions d'agents, au plus près de l'œil", () => {
    // Mesuré sur la vraie stack (#1182) : le fil colle à son bas, et une question
    // d'agent — déjà repartie sans réponse — y occupait la vue pendant que la
    // décision qui retient le run entier restait au-dessus, hors de l'écran.
    poserChemin("/chat");
    poserFilAssistance({ messages: [] });
    rendreAvecEtat(<PageChat />, {
      executions: [runAuPlafond()],
      questions: [questionFactice({ projet_id: null })],
    });

    const fil = screen.getByRole("region", { name: "Chat global" });
    const question = within(fil).getByRole("region", { name: "Question de l'agent bdd" });
    const carte = within(fil).getByRole("region", { name: "Budget du run atteint" });
    expect(
      question.compareDocumentPosition(carte) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("la carte est posée au pied du fil de /chat, et agit de là", async () => {
    const trancherPlafond = vi.fn(async () => {});
    poserChemin("/chat");
    poserFilAssistance({ messages: [] });
    rendreAvecEtat(<PageChat />, { executions: [runAuPlafond()], trancherPlafond });

    const fil = screen.getByRole("region", { name: "Chat global" });
    const carte = within(fil).getByRole("region", { name: "Budget du run atteint" });
    await userEvent.click(within(carte).getByRole("button", { name: "Arrêter le run" }));

    await waitFor(() =>
      expect(trancherPlafond).toHaveBeenCalledWith("run-plafond", { geste: GESTE_ARRETER }),
    );
  });

  it("passe le filet d'accessibilité, carte montée dans son shell", async () => {
    // Le seuil du dépôt (#537) : aucune violation `serious`/`critical` sur le
    // document entier — les cases à cocher, le champ et ses messages compris.
    poserChemin("/chat");
    poserFilAssistance({ messages: [] });
    rendreAvecEtat(<PageChat />, { executions: [runAuPlafond()] });
    screen.getByRole("region", { name: "Budget du run atteint" });

    const trouvees = bloquantes(await auditerLaPage());
    expect(trouvees, raconter(trouvees)).toHaveLength(0);
  });

  it("un run qui n'est plus au plafond ne laisse aucune carte", () => {
    poserChemin("/chat");
    poserFilAssistance({ messages: [] });
    rendreAvecEtat(<PageChat />, {
      executions: [runFactice({ run_id: "run-plafond", statut: EXECUTION_EN_COURS })],
    });

    expect(screen.queryByRole("region", { name: "Budget du run atteint" })).toBeNull();
  });
});

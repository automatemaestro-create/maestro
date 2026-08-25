/**
 * La liste des runs du projet actif, et les règles qu'elle partage (#474/#477/#479 ;
 * couvert ici par #480, lot 8 de #472).
 *
 * Trois volets, du plus profond au plus visible :
 *
 * ① **Le régime d'un run** (`lib/execution`) — « en cours » au sens de l'API
 *    recouvre un run qui avance *et* un run arrêté depuis trois heures sur une
 *    question : c'est le défaut d'origine du chantier, **53 minutes perdues le
 *    2026-08-14** (#355). `regimeDuRun` sépare ; **l'ordre dans lequel il décide
 *    est la décision**, donc c'est lui qu'on éprouve, cas par cas.
 * ② **La brique partagée** (`CarteRun`) — badge, avancement, attente, cause,
 *    interruption, pause. Elle est rendue par **trois** écrans, et un run lu
 *    « Brief à valider » ici et « En cours » ailleurs serait un run dont on doute.
 * ③ **L'écran** (`ListeRuns`) — ce qu'il montre plein, chargé, vide, et en panne :
 *    ces quatre états ne se confondent pas, c'est tout l'argument du poste vide
 *    (#186/#281).
 *
 * Aucun réseau : `useControlTower` est mocké par `tests/setup.ts`, et l'état du
 * shell se pose au deuxième argument de `rendreAvecEtat`.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ListeRuns } from "@/components/runs/ListeRuns";
import {
  ATTENTES,
  Avancement,
  BoutonInterrompre,
  BoutonsPause,
  CarteRun,
  GestesRun,
  LigneCause,
  LignePause,
  fondDe,
} from "@/components/runs/EtatRun";
import {
  ATTENTE_BRIEF,
  ATTENTE_REPONSES,
  ATTENTE_VALIDATION,
  REGIME_EN_PAUSE,
  REGIME_INTERROMPU,
  REGIME_SOLDE,
  REGIME_SUSPENDU,
  REGIME_TRAVAILLE,
  causeDAttente,
  estEnPause,
  estSolde,
  peutEtreInterrompu,
  peutEtreSuspendu,
  regimeDuRun,
  runsEnAttenteDeValidation,
  type CauseAttente,
} from "@/lib/execution";
import { libelleCause, libelleStatutExecution } from "@/lib/format";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import {
  CAUSE_ANNULATION,
  CAUSE_HOTE_NON_DEMARRE,
  CAUSE_LIMITE_USAGE,
  CAUSE_PLAFOND_COUT,
  CAUSE_PLAFOND_TOURS,
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
  VITALITE_ORPHELIN,
  VITALITE_VIVANT,
  type ResumeExecution,
} from "@/lib/types";

import {
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

// --------------------------------------- ① Le régime d'un run : l'ordre décide

describe("le régime d'un run — ce que « en cours » cachait", () => {
  it("sépare ce qui avance de ce qui attend un humain", () => {
    expect(regimeDuRun(runFactice())).toBe(REGIME_TRAVAILLE);
    expect(
      regimeDuRun(runFactice({ statut: EXECUTION_EN_ATTENTE_BRIEF })),
    ).toBe(REGIME_SUSPENDU);
    expect(
      regimeDuRun(runFactice({ statut: EXECUTION_EN_ATTENTE_REPONSES })),
    ).toBe(REGIME_SUSPENDU);
  });

  it("range la troisième attente, qui ne se lit pas sur le run", () => {
    // Une demande de validation porte sa **tâche**, jamais son run, et le statut
    // du run reste `en_cours` pendant qu'elle dort : l'appariement passe par les
    // tâches, sur les deux listes que le shell tient déjà.
    expect(regimeDuRun(runFactice(), true)).toBe(REGIME_SUSPENDU);
    expect(causeDAttente(runFactice(), true)).toBe(ATTENTE_VALIDATION);
  });

  it.each([
    [EXECUTION_TERMINEE],
    [EXECUTION_ANNULEE],
    [EXECUTION_ECHEC],
  ])("tient un run %s pour soldé, quoi qu'il traîne derrière lui", (statut) => {
    // Soldé passe **en premier** : une validation restée ouverte sur un run fini
    // ne le fait pas repasser pour suspendu.
    expect(regimeDuRun(runFactice({ statut }), true)).toBe(REGIME_SOLDE);
    expect(estSolde(runFactice({ statut }))).toBe(true);
  });

  it("fait passer un orphelin devant une attente — le seul arbitrage discutable", () => {
    // Un orphelin arrêté sur son brief *attend* bien, mais personne ne recevra la
    // réponse : il faut le **reprendre** (#349), pas lui répondre.
    const perdu = runFactice({
      statut: EXECUTION_EN_ATTENTE_BRIEF,
      vitalite: VITALITE_ORPHELIN,
    });

    expect(regimeDuRun(perdu)).toBe(REGIME_INTERROMPU);
  });

  it("fait passer la pause devant l'attente, et l'interruption devant la pause", () => {
    // Un run suspendu **pendant** l'attente de son brief porte les deux faits :
    // la pause est un drapeau à côté du statut, pas dedans (#477).
    expect(
      regimeDuRun(
        runFactice({ statut: EXECUTION_EN_ATTENTE_BRIEF, en_pause: true }),
      ),
    ).toBe(REGIME_EN_PAUSE);
    expect(
      regimeDuRun(
        runFactice({ en_pause: true, vitalite: VITALITE_ORPHELIN }),
      ),
    ).toBe(REGIME_INTERROMPU);
  });

  it("ne fait pas disparaître un run dont le statut lui est inconnu", () => {
    expect(regimeDuRun(runFactice({ statut: "venu-du-futur" }))).toBe(
      REGIME_TRAVAILLE,
    );
  });

  it("n'apparie une validation que si elle est en attente et porte une tâche connue", () => {
    const taches = [tacheFactice({ id: "T-1", run_id: "run-1" })];

    expect(
      runsEnAttenteDeValidation([validationFactice({ tache_id: "T-1" })], taches),
    ).toEqual(new Set(["run-1"]));
    // Une validation déjà tranchée n'attend plus personne.
    expect(
      runsEnAttenteDeValidation(
        [validationFactice({ tache_id: "T-1", statut: "approuve" })],
        taches,
      ).size,
    ).toBe(0);
    // Une tâche sans run ne rattache rien : mieux vaut ne rien dire que deviner.
    expect(
      runsEnAttenteDeValidation(
        [validationFactice({ tache_id: "T-1" })],
        [tacheFactice({ id: "T-1", run_id: "" })],
      ).size,
    ).toBe(0);
  });

  it("sait qui peut être suspendu, et qui ne le peut pas", () => {
    expect(peutEtreSuspendu(runFactice())).toBe(true);
    expect(peutEtreSuspendu(runFactice({ statut: EXECUTION_TERMINEE }))).toBe(false);
    expect(peutEtreSuspendu(runFactice({ vitalite: VITALITE_ORPHELIN }))).toBe(false);
    expect(peutEtreSuspendu(runFactice({ en_pause: true }))).toBe(false);
    expect(estEnPause(runFactice({ en_pause: true }))).toBe(true);
    // Le contrat garde `en_pause` **facultatif** : un backend d'avant #477 n'en
    // porte pas, et son absence vaut « pas en pause », jamais un écran cassé.
    expect(estEnPause(runFactice())).toBe(false);
  });
});

describe("les libellés que la liste emprunte au format", () => {
  it("nomme chaque statut de run, et laisse passer l'inconnu", () => {
    expect(libelleStatutExecution(EXECUTION_EN_COURS)).toBe("En cours");
    expect(libelleStatutExecution(EXECUTION_EN_ATTENTE_BRIEF)).toBe("Brief à valider");
    expect(libelleStatutExecution("venu-du-futur")).toBe("venu-du-futur");
  });

  it("nomme les cinq causes d'arrêt et se tait sur ce qu'elle ne sait pas (#479)", () => {
    expect(libelleCause(CAUSE_PLAFOND_TOURS)).toBe("Plafond de tours atteint");
    expect(libelleCause(CAUSE_PLAFOND_COUT)).toBe("Plafond de dépense atteint");
    expect(libelleCause(CAUSE_LIMITE_USAGE)).toBe("Limite d'usage du fournisseur");
    expect(libelleCause(CAUSE_HOTE_NON_DEMARRE)).toBe("L'hôte du run n'a pas démarré");
    expect(libelleCause(CAUSE_ANNULATION)).toBe("Interrompu");
    // Une cause absente ou venue d'un backend plus récent ne rend **rien** : la
    // ligne disparaît, plutôt que d'afficher un code brut à l'écran.
    expect(libelleCause(undefined)).toBeNull();
    expect(libelleCause("")).toBeNull();
    expect(libelleCause("venue-du-futur")).toBeNull();
  });
});

describe("le chemin d'un run — dérivé du menu, jamais écrit en dur", () => {
  it("vit sous l'entrée de sa liste", () => {
    expect(hrefRun("run-1")).toBe(`${entreeParLibelle("Runs")?.href}/run-1`);
  });

  it("encode ce qu'un identifiant pourrait porter", () => {
    expect(hrefRun("a/b")).toBe("/runs/a%2Fb");
  });
});

// ---------------------------------- ② La brique partagée : ce qu'une carte dit

describe("la carte d'un run — la même sur les trois écrans", () => {
  const carte = (run: ResumeExecution, attendUneValidation = false) =>
    rendreAvecEtat(
      <ul>
        <CarteRun run={run} attendUneValidation={attendUneValidation} />
      </ul>,
    );

  it("mène à la vue du run par son titre", () => {
    carte(runFactice({ run_id: "3ff0bcb", objectif: "Prototyper un mini-CRM" }));

    expect(
      screen.getByRole("link", { name: "Prototyper un mini-CRM" }),
    ).toHaveAttribute("href", "/runs/3ff0bcb");
  });

  it("se rabat sur l'identifiant quand le run n'a pas d'objectif", () => {
    carte(runFactice({ run_id: "3ff0bcb", objectif: "" }));

    expect(screen.getByRole("link", { name: "3ff0bcb" })).toBeInTheDocument();
  });

  it.each([
    [runFactice(), "En cours"],
    [runFactice({ en_pause: true }), "En pause"],
    [runFactice({ statut: EXECUTION_EN_ATTENTE_BRIEF }), "Brief à valider"],
    [runFactice({ statut: EXECUTION_EN_ATTENTE_REPONSES }), "Questions en attente"],
    [runFactice({ vitalite: VITALITE_ORPHELIN }), "Interrompu"],
    [runFactice({ statut: EXECUTION_TERMINEE }), "Terminée"],
    [runFactice({ statut: EXECUTION_ECHEC }), "Échec"],
  ])("porte un badge qui dit son régime", (run, libelle) => {
    carte(run);

    expect(screen.getByText(libelle)).toBeInTheDocument();
  });

  it("mène l'attente vers l'écran qui porte le geste qui la lève", () => {
    // La vue d'un run *montre* l'attente sans la débloquer : le renvoi vise
    // « Valider le brief » ou « Validations », jamais la vue elle-même.
    carte(runFactice({ statut: EXECUTION_EN_ATTENTE_BRIEF }));

    expect(screen.getByText(ATTENTES[ATTENTE_BRIEF].phrase)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: new RegExp(ATTENTES[ATTENTE_BRIEF].action) }),
    ).toHaveAttribute(
      "href",
      entreeParLibelle(ATTENTES[ATTENTE_BRIEF].page)?.href ?? "",
    );
  });

  it("dit la cause d'un run tombé, en plus de son détail (#479)", () => {
    carte(runFactice({ statut: EXECUTION_ECHEC, cause: CAUSE_LIMITE_USAGE }));

    expect(screen.getByText("Limite d'usage du fournisseur")).toBeInTheDocument();
  });

  it("explique ce qu'une pause fait, et ce qu'elle ne fait pas (#477)", () => {
    carte(runFactice({ en_pause: true }));

    expect(
      screen.getByText(/Aucune tâche nouvelle n'est lancée/),
    ).toBeInTheDocument();
    expect(screen.getByText(/vont à leur terme/)).toBeInTheDocument();
  });

  it("distingue un run interrompu récupérable de celui qui ne l'est pas", () => {
    carte(runFactice({ vitalite: VITALITE_ORPHELIN, brief_approuve: true }));
    expect(screen.getByText(/il peut repartir/)).toBeInTheDocument();

    carte(runFactice({ vitalite: VITALITE_ORPHELIN, brief_approuve: false }));
    expect(screen.getByText(/rien ne s'y joue plus/)).toBeInTheDocument();
  });

  it("mène au run qu'il reprend, sans quoi le cadrage déjà payé serait hors de portée", () => {
    rendreAvecEtat(
      <ul>
        <CarteRun run={runFactice({ reprise_de: "" })} attendUneValidation={false} />
      </ul>,
    );
    expect(screen.queryByText(/Reprise de/)).not.toBeInTheDocument();
  });

  it("teinte le fond d'un run qui attend, et lui seul", () => {
    expect(fondDe(REGIME_SUSPENDU)).not.toBe(fondDe(REGIME_TRAVAILLE));
    expect(fondDe(REGIME_EN_PAUSE)).toBe(fondDe(REGIME_TRAVAILLE));
  });
});

describe("l'avancement d'un run — compté par le backend, jamais ici", () => {
  const barre = (run: ResumeExecution) => rendreAvecEtat(<Avancement run={run} />);

  it("rend les six compartiments dans l'ordre du flux de travail", () => {
    barre(
      runFactice({
        nb_taches: 7,
        progression: {
          a_faire: 2,
          en_cours: 1,
          bloquees: 0,
          terminees: 3,
          echecs: 1,
          autres: 0,
          soldees: 4,
          total: 7,
        },
      }),
    );

    const jauge = screen.getByRole("progressbar", { name: "Progression du run" });
    expect(jauge).toHaveAttribute("aria-valuenow", "4");
    expect(jauge).toHaveAttribute("aria-valuemax", "7");
    expect(jauge).toHaveAttribute("aria-valuetext", "4 tâches soldées sur 7");
    expect(
      screen.getByText("3 terminées · 1 échec · 1 en cours · 2 à faire — 4/7 soldées"),
    ).toBeInTheDocument();
  });

  it("se rabat sur le nombre de tâches quand la progression manque", () => {
    // La progression est **optionnelle** dans le contrat : une trace d'un backend
    // antérieur n'en porte pas. Dire « 8 tâches » sans savoir où elles en sont
    // vaut mieux qu'une barre inventée.
    barre(runFactice({ nb_taches: 8 }));

    expect(screen.getByText("8 tâches")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });

  it("dit qu'un run n'a aucune tâche plutôt que de se taire", () => {
    // L'état normal d'un run arrêté sur son brief, pas le symptôme d'une lecture
    // ratée.
    barre(runFactice({ nb_taches: 0 }));

    expect(screen.getByText("Aucune tâche")).toBeInTheDocument();
  });
});

describe("les ordres de pause — le seul geste que la carte porte", () => {
  it("suspend le run sur lequel on a cliqué, et lui seul", async () => {
    const suspendre = vi.fn().mockResolvedValue(runFactice({ en_pause: true }));
    rendreAvecEtat(<BoutonsPause run={runFactice({ run_id: "run-7" })} />, {
      suspendreRun: suspendre,
    });

    await userEvent.click(screen.getByRole("button", { name: "Mettre en pause" }));

    await waitFor(() => expect(suspendre).toHaveBeenCalledTimes(1));
    expect(suspendre).toHaveBeenCalledWith("run-7");
  });

  it("reprend un run suspendu — l'autre moitié du même bouton", async () => {
    const reprendre = vi.fn().mockResolvedValue(runFactice());
    rendreAvecEtat(
      <BoutonsPause run={runFactice({ run_id: "run-7", en_pause: true })} />,
      { reprendreRun: reprendre },
    );

    await userEvent.click(screen.getByRole("button", { name: "Reprendre" }));

    await waitFor(() => expect(reprendre).toHaveBeenCalledWith("run-7"));
  });

  it("dit le refus de l'API au lieu de le taire", async () => {
    const suspendre = vi
      .fn()
      .mockRejectedValue(new Error("exécution déjà soldée (terminee)"));
    rendreAvecEtat(<BoutonsPause run={runFactice()} />, { suspendreRun: suspendre });

    await userEvent.click(screen.getByRole("button", { name: "Mettre en pause" }));

    expect(
      await screen.findByText(/exécution déjà soldée/),
    ).toBeInTheDocument();
  });

  it.each([
    [runFactice({ statut: EXECUTION_TERMINEE })],
    [runFactice({ vitalite: VITALITE_ORPHELIN })],
  ])("ne propose rien sur un run qu'on ne peut pas suspendre", (run) => {
    const { container } = rendreAvecEtat(<BoutonsPause run={run} />);

    expect(container.querySelector("button")).toBeNull();
  });
});

describe("interrompre un run — le geste qui manquait (#467)", () => {
  it.each([
    [EXECUTION_EN_COURS, VITALITE_VIVANT],
    // L'orphelin, et c'est **la** divergence avec la pause : celle-ci l'écarte
    // parce que personne ne recevrait l'ordre, l'annulation n'a pas besoin qu'il
    // soit reçu — l'API borne son attente et solde le run de toute façon. C'est
    // le cas des quatre fantômes soldés au `curl` le 2026-08-24.
    [EXECUTION_EN_COURS, VITALITE_ORPHELIN],
    // En vol mais arrêté sur un humain : il tient un hôte et du cadrage payé.
    [EXECUTION_EN_ATTENTE_BRIEF, VITALITE_VIVANT],
    [EXECUTION_EN_ATTENTE_REPONSES, VITALITE_VIVANT],
  ])("s'applique à tout run non soldé (%s, %s)", (statut, vitalite) => {
    expect(peutEtreInterrompu(runFactice({ statut, vitalite }))).toBe(true);
  });

  it("laisse la pause et l'interruption diverger sur l'orphelin, à dessein", () => {
    const fantome = runFactice({ vitalite: VITALITE_ORPHELIN });

    expect(peutEtreSuspendu(fantome)).toBe(false);
    expect(peutEtreInterrompu(fantome)).toBe(true);
  });

  it.each([[EXECUTION_TERMINEE], [EXECUTION_ANNULEE], [EXECUTION_ECHEC]])(
    "ne pose pas la question sur un run déjà soldé (%s)",
    (statut) => {
      // L'API répond 409 : proposer le geste serait offrir un clic dont on connaît
      // déjà le refus.
      expect(peutEtreInterrompu(runFactice({ statut }))).toBe(false);

      const { container } = rendreAvecEtat(
        <BoutonInterrompre run={runFactice({ statut })} />,
      );
      expect(container.querySelector("button")).toBeNull();
    },
  );

  it("ne part pas au premier clic : il faut confirmer", async () => {
    const interrompre = vi.fn().mockResolvedValue(runFactice());
    rendreAvecEtat(<BoutonInterrompre run={runFactice({ run_id: "run-9" })} />, {
      interrompreRun: interrompre,
    });

    await userEvent.click(screen.getByRole("button", { name: /Interrompre/ }));

    // Armé, pas parti — c'est tout le sujet du critère : le geste est
    // irréversible et coûte le travail en cours.
    expect(interrompre).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", { name: "Confirmer l'interruption" }),
    );

    await waitFor(() => expect(interrompre).toHaveBeenCalledTimes(1));
    expect(interrompre).toHaveBeenCalledWith("run-9");
  });

  it("dit ce qu'on perd, et seulement une fois armé", async () => {
    // Sur une liste de vingt runs, l'afficher d'office rendrait l'avertissement
    // invisible à force d'être partout.
    const perte = /perdent leur travail/;
    rendreAvecEtat(<BoutonInterrompre run={runFactice()} />);

    expect(screen.queryByText(perte)).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Interrompre/ }));

    expect(screen.getByText(perte)).toBeInTheDocument();
  });

  it("se ravise sans rien envoyer", async () => {
    const interrompre = vi.fn();
    rendreAvecEtat(<BoutonInterrompre run={runFactice()} />, {
      interrompreRun: interrompre,
    });

    await userEvent.click(screen.getByRole("button", { name: /Interrompre/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Laisser tourner" }),
    );

    expect(interrompre).not.toHaveBeenCalled();
    // Et le bouton d'origine est revenu : se raviser ne condamne pas le geste.
    expect(
      screen.getByRole("button", { name: /Interrompre/ }),
    ).toBeInTheDocument();
  });

  it("dit le refus de l'API au lieu de le taire", async () => {
    // Le 409 qu'on ne peut pas prévoir : le run s'est soldé entre l'affichage et
    // le clic.
    const interrompre = vi
      .fn()
      .mockRejectedValue(new Error("exécution déjà soldée (terminee) : run-1."));
    rendreAvecEtat(<BoutonInterrompre run={runFactice()} />, {
      interrompreRun: interrompre,
    });

    await userEvent.click(screen.getByRole("button", { name: /Interrompre/ }));
    await userEvent.click(
      screen.getByRole("button", { name: "Confirmer l'interruption" }),
    );

    expect(await screen.findByText(/exécution déjà soldée/)).toBeInTheDocument();
    // Désarmé : recliquer sur une confirmation qu'on vient de voir refuser
    // rendrait le même refus.
    expect(
      screen.queryByRole("button", { name: "Confirmer l'interruption" }),
    ).not.toBeInTheDocument();
  });
});

describe("les gestes d'un run, sur une même rangée", () => {
  it("met la pause et l'interruption côte à côte sur un run qui travaille", () => {
    rendreAvecEtat(<GestesRun run={runFactice()} />);

    expect(
      screen.getByRole("button", { name: "Mettre en pause" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Interrompre/ }),
    ).toBeInTheDocument();
  });

  it("ne garde que l'interruption sur un orphelin", () => {
    // La pause n'y arriverait pas ; l'annulation, si — et c'est le seul moyen
    // d'éteindre un fantôme depuis l'interface.
    rendreAvecEtat(
      <GestesRun run={runFactice({ vitalite: VITALITE_ORPHELIN })} />,
    );

    expect(
      screen.queryByRole("button", { name: "Mettre en pause" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Interrompre/ }),
    ).toBeInTheDocument();
  });

  it("garde les deux sur un run en pause : la pause n'est pas une issue", () => {
    rendreAvecEtat(<GestesRun run={runFactice({ en_pause: true })} />);

    expect(
      screen.getByRole("button", { name: "Reprendre" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Interrompre/ }),
    ).toBeInTheDocument();
  });

  it("disparaît entièrement sur un run soldé", () => {
    const { container } = rendreAvecEtat(
      <GestesRun run={runFactice({ statut: EXECUTION_TERMINEE })} />,
    );

    expect(container.textContent).toBe("");
  });
});

describe("les lignes qui ne s'affichent que quand elles ont quelque chose à dire", () => {
  it("se taisent toutes sur un run ordinaire", () => {
    const { container } = rendreAvecEtat(
      <>
        <LigneCause run={runFactice()} />
        <LignePause regime={REGIME_TRAVAILLE} />
      </>,
    );

    expect(container.textContent).toBe("");
  });
});

// ----------------------------------------------- ③ L'écran, dans ses quatre états

describe("la liste des runs (l'écran)", () => {
  const monter = (partiel = {}, projet = projetFactice({ nom: "Dépensio" })) =>
    rendreAvecEtat(<ListeRuns />, partiel, projet);

  it("nomme le projet, compte les runs et garde l'ordre du backend", () => {
    // L'ordre vient de `GET /api/executions` (récents d'abord) : retrier ici
    // poserait une seconde règle à tenir d'accord avec la première, pour un
    // résultat identique.
    monter({
      executions: [
        runFactice({ run_id: "recent", objectif: "Le plus récent" }),
        runFactice({ run_id: "ancien", objectif: "Le plus ancien" }),
      ],
    });

    const liste = screen.getByRole("region", { name: "Runs du projet" });
    expect(within(liste).getByRole("heading", { level: 2 })).toHaveTextContent(
      "Runs de Dépensio",
    );
    const cartes = within(liste).getAllByRole("listitem");
    expect(cartes).toHaveLength(2);
    expect(cartes[0]).toHaveTextContent("Le plus récent");
  });

  it("apparie chaque validation en attente au run de sa tâche", () => {
    monter({
      executions: [
        runFactice({ run_id: "run-1" }),
        runFactice({ run_id: "run-2" }),
      ],
      taches: [tacheFactice({ id: "T-9", run_id: "run-2" })],
      validations: [validationFactice({ tache_id: "T-9" })],
    });

    const [premier, second] = screen.getAllByRole("listitem");
    expect(premier).toHaveTextContent("En cours");
    expect(second).toHaveTextContent(ATTENTES[ATTENTE_VALIDATION].libelle);
  });

  it("dit qu'il charge plutôt que de montrer un vide", () => {
    monter({ chargement: true });

    expect(screen.getByText("Chargement des runs…")).toBeInTheDocument();
    expect(
      screen.queryByRole("region", { name: "Runs du projet" }),
    ).not.toBeInTheDocument();
  });

  it("nomme le projet quand il n'a pas encore de run, et propose d'en lancer un", () => {
    monter();

    expect(screen.getByText(/Aucun run sur Dépensio/)).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Composer un objectif" }),
    ).toHaveAttribute("href", entreeParLibelle("Composer un objectif")?.href);
  });

  it("ne conseille pas de lancer un run à qui n'a pas de backend", () => {
    // C'est l'argument du poste vide (#186) : une API injoignable garde sa
    // bannière et **rien d'autre** — un écran vide *et muet* ne se diagnostique
    // pas comme un écran vide *et connecté*.
    monter({ erreur: "connexion refusée" });

    expect(screen.getByRole("alert")).toHaveTextContent(/API injoignable/);
    expect(
      screen.queryByRole("link", { name: "Composer un objectif" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText(/Aucun run sur/)).not.toBeInTheDocument();
  });
});

describe("les attentes et leurs renvois", () => {
  it("désignent leur écran par son libellé de menu, jamais par un chemin", () => {
    // C'est la règle de #191 tenue dans l'autre sens : une page qui déménage
    // emmène son renvoi avec elle. La table ne connaît donc que des libellés,
    // résolus par le menu — et un libellé qui ne résout plus rien casserait le
    // renvoi en silence.
    const attentes: CauseAttente[] = [
      ATTENTE_BRIEF,
      ATTENTE_REPONSES,
      ATTENTE_VALIDATION,
    ];
    for (const attente of attentes) {
      expect(entreeParLibelle(ATTENTES[attente].page)).toBeDefined();
    }
  });

  it("mènent chacune à l'écran qui porte le geste", () => {
    // Deux attentes se lèvent au même endroit — c'est le même écran qui porte le
    // brief et ses questions —, la troisième ailleurs.
    expect(entreeParLibelle(ATTENTES[ATTENTE_BRIEF].page)?.href).toBe("/brief");
    expect(entreeParLibelle(ATTENTES[ATTENTE_REPONSES].page)?.href).toBe("/brief");
    expect(entreeParLibelle(ATTENTES[ATTENTE_VALIDATION].page)?.href).toBe(
      "/validations",
    );
  });
});

describe("un run vivant n'est pas un run orphelin", () => {
  it("ne prend pas la vitalité par défaut pour une interruption", () => {
    expect(regimeDuRun(runFactice({ vitalite: VITALITE_VIVANT }))).toBe(
      REGIME_TRAVAILLE,
    );
    expect(regimeDuRun(runFactice({ vitalite: null }))).toBe(REGIME_TRAVAILLE);
  });
});

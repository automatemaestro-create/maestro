/**
 * Le **bilan** d'un run dans sa vue (#1285, lot 3 de #1281 ; docs/05 §6.23).
 *
 * Le run réel `3fe501fc0878` (`p3`) a échoué trois fois à l'identique pendant que
 * le moteur présumait un aléa, et rien à l'écran ne disait ce qui avait failli ni
 * pourquoi. Le lot 2 (#1284) fait rendre un bilan sur pièces à la fin de tout run ;
 * cet écran le montre. Ce que ces tests gardent, c'est ce qui ferait retomber la
 * vue dans le défaut sans rien casser :
 *
 * ① **la tête dit le bilan en une ligne** — la nature des échecs comprise, parce
 *    que « se reproduira » est la réponse que `p3` n'a pas eue —, et y mène ;
 * ② **le travers d'abord** : les rubriques dans l'ordre de lecture, une rubrique
 *    vide absente, la nature d'un échec portée par un mot (jamais la teinte seule) ;
 * ③ **chaque constat mène à ses pièces**, rendues en clair, et chaque pièce
 *    s'ouvre dans le journal **sur ses entrées** (lues par leur identifiant) ou
 *    dans la frise quand elle y figure ;
 * ④ **les cinq états du ticket** ne se confondent pas : un run en échec, un run
 *    terminé sans défaut, un bilan en rédaction, un bilan absent (avec sa raison
 *    quand l'API la sait), l'API en erreur — et un run en vol ne demande rien ;
 * ⑤ **ce qui a été écarté reste visible**, avec sa raison ;
 * ⑥ **sobriété et accessibilité** : aucun bloc de plus (la lecture vit dans la
 *    bascule), aucune violation `serious`/`critical`.
 *
 * Les bilans ci-dessous reprennent, abrégés, ceux que le vrai modèle a rendus au
 * passage du banc du 2026-09-27 (S4, run arrêté par un plafond de tokens à 1 ;
 * S1, un dossier vidé) — pas des bilans imaginés.
 *
 * Réseau débranché comme partout (`tests/setup.ts`) : les lectures sont mockées
 * ici, la vue est la vraie.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VueRun } from "@/components/runs/VueRun";
import { ErreurApi } from "@/lib/api";
import { resumeEvenement } from "@/lib/evenements";
import { evenementDepuisEntree } from "@/lib/journal";
import {
  entreesALire,
  estUneLigneDuJournal,
  etatDuBilan,
  piecesDuConstat,
  resumeDuBilan,
  rubriquesDuBilan,
} from "@/lib/bilan";
import {
  ETAT_BILAN_ABSENT,
  ETAT_BILAN_EN_REDACTION,
  ETAT_BILAN_RENDU,
  EVENEMENT_EXECUTION_STATUT,
  EVENEMENT_TACHE_STATUT,
  EXECUTION_ECHEC,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
  RAISON_BILAN_MODELE_MUET,
  RAISON_BILAN_REPONSE_ILLISIBLE,
  STATUT_BILAN_RENDU,
  type BilanRun,
  type EntreeJournal,
  type FriseRun,
  type PageJournal,
  type ReponseBilan,
  type Tache,
} from "@/lib/types";

import { auditerLaPage, bloquantes, raconter } from "./axe";
import {
  bilanFactice,
  entreeFriseFactice,
  entreeJournalFactice,
  friseFactice,
  grapheFactice,
  pageJournalCourante,
  projetFactice,
  rendreAvecEtat,
  reponseBilanFactice,
  runFactice,
  tacheFactice,
} from "./aides";
import { BLOCS_MAX, placesDe } from "./places";

const RUN = "a76cdf2bbd2b";
const TACHE = "rediger-notes-md";
const AUTRE_TACHE = "relire-notes-md";

/** Ce que les fausses lectures rendront, et ce qu'on leur a demandé. */
const lecture = vi.hoisted(() => ({
  bilan: null as ReponseBilan | null,
  echecBilan: null as Error | null,
  appelsBilan: [] as string[],
  entrees: [] as EntreeJournal[],
  appelsJournal: [] as { runId?: string; ids?: readonly string[] }[],
  frise: null as FriseRun | null,
  taches: [] as Tache[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits : ce mock **remplace** celui de `tests/setup.ts`.
    chargerProjets: async () => [],
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => grapheFactice({ run_id: RUN }),
    chargerFriseExecution: async () => lecture.frise ?? friseFactice({ run_id: RUN }),
    chargerBilanExecution: async (runId: string) => {
      lecture.appelsBilan.push(runId);
      if (lecture.echecBilan !== null) throw lecture.echecBilan;
      return lecture.bilan ?? reponseBilanFactice({ run_id: runId });
    },
    // Le journal sert ce qu'on lui demande : les entrées nommées quand `ids`
    // est là (une pièce du bilan), toutes sinon — comme l'API réelle.
    chargerJournal: async (
      _portee: string,
      options: { runId?: string; ids?: readonly string[] } = {},
    ): Promise<PageJournal> => {
      lecture.appelsJournal.push({ runId: options.runId, ids: options.ids });
      const entrees =
        options.ids === undefined
          ? lecture.entrees
          : lecture.entrees.filter((entree) => options.ids?.includes(entree.id));
      return { ...pageJournalCourante(), entrees, total: entrees.length };
    },
  };
});

// --- Le run en échec du banc (S4), abrégé ---------------------------------

const ENTREE_ECHEC = entreeJournalFactice({
  id: "j-0055",
  type: EVENEMENT_TACHE_STATUT,
  run_id: RUN,
  tache_id: TACHE,
  titre: "Créer NOTES.md : le projet décrit en trois lignes",
  // Le repère que le moteur consigne sur une tâche jamais exécutée — pas un agent.
  agent: "—",
  role: "non exécutée",
  statut: "echec",
  detail:
    "plafond de tokens dépassé : 36809 tokens consommés sur l'exécution pour un plafond de 1 — tâche stoppée.",
  horodatage: "2026-09-27T16:41:02+00:00",
});

const ENTREE_FIN = entreeJournalFactice({
  id: "j-0056",
  type: EVENEMENT_EXECUTION_STATUT,
  run_id: RUN,
  statut: EXECUTION_ECHEC,
  detail: "0/1 tâche(s) réussie(s)",
  horodatage: "2026-09-27T16:41:02+00:00",
});

/** Le bilan de S4 : ce qui a failli se reproduira, deux choses à changer, un acte. */
function bilanEchec(partiel: Partial<BilanRun> = {}): BilanRun {
  return bilanFactice({
    run_id: RUN,
    statut: EXECUTION_ECHEC,
    constats: [
      {
        rubrique: "livre",
        texte: "Le run a seulement fait le cadrage et la planification. Le fichier NOTES.md n'a pas été livré.",
        pieces: ["P10"],
        nature: "",
        tache: "",
        agent: "",
        revision_playbook: false,
      },
      {
        rubrique: "echec",
        texte: "La tâche a été arrêtée avant de commencer, parce que la limite de tokens fixée au lancement était de 1. Relancé à l'identique, le run s'arrêterait au même endroit.",
        pieces: ["P9", "P2"],
        nature: "deterministe",
        tache: TACHE,
        agent: "",
        revision_playbook: false,
      },
      {
        rubrique: "recommandation",
        texte: "Redemandez la création de NOTES.md avec une limite de tokens nettement supérieure, ou sans limite.",
        pieces: ["P9"],
        nature: "",
        tache: TACHE,
        agent: "",
        revision_playbook: false,
      },
      {
        rubrique: "recommandation",
        texte: "Vérifiez qu'aucun NOTES.md partiel n'est resté à la racine du projet.",
        pieces: ["P8"],
        nature: "",
        tache: TACHE,
        agent: "",
        revision_playbook: false,
      },
      {
        rubrique: "acte",
        texte: "La tâche devait écrire directement dans le dossier du projet, sans étape de validation.",
        pieces: ["P8"],
        nature: "",
        tache: TACHE,
        agent: "",
        revision_playbook: false,
      },
    ],
    pieces: [
      {
        id: "P2",
        famille: "usage",
        texte:
          "Usage de la tâche « Créer NOTES.md : le projet décrit en trois lignes » (rediger-notes-md) : 0 tokens, coût inconnu, 0 s ; issue : Échec",
        tache_id: TACHE,
        entrees: ["j-0055"],
        synthese: true,
        libelle:
          "« Créer NOTES.md : le projet décrit en trois lignes » a consommé 0 tokens, coût inconnu, en 0 s — issue : Échec.",
      },
      {
        id: "P8",
        famille: "acte",
        texte: "2026-09-27T16:41:02+00:00 · agent.activite · statut ecriture_en_place · …",
        tache_id: TACHE,
        entrees: ["j-0054"],
        synthese: false,
      },
      {
        id: "P9",
        famille: "statut",
        texte: "2026-09-27T16:41:02+00:00 · tache.statut · tâche rediger-notes-md · statut echec · plafond de tokens dépassé",
        tache_id: TACHE,
        entrees: ["j-0055"],
        synthese: false,
      },
      {
        id: "P10",
        famille: "statut",
        texte: "2026-09-27T16:41:02+00:00 · execution.statut · statut echec · 0/1 tâche(s) réussie(s)",
        tache_id: "",
        entrees: ["j-0056"],
        synthese: false,
      },
    ],
    pieces_offertes: 10,
    entrees_lues: 8,
    ...partiel,
  });
}

beforeEach(() => {
  lecture.bilan = reponseBilanFactice({
    run_id: RUN,
    bilan: bilanEchec(),
    etat: ETAT_BILAN_RENDU,
  });
  lecture.echecBilan = null;
  lecture.appelsBilan.length = 0;
  lecture.entrees = [ENTREE_ECHEC, ENTREE_FIN];
  lecture.appelsJournal.length = 0;
  lecture.frise = friseFactice({
    run_id: RUN,
    entrees: [
      entreeFriseFactice({
        id: "j-0055",
        type: EVENEMENT_TACHE_STATUT,
        tache_id: TACHE,
        titre: "Créer NOTES.md : le projet décrit en trois lignes",
        statut: "echec",
        objet: "plafond de tokens dépassé",
      }),
    ],
  });
  // Deux tâches : la ligne « Tâche : » ne se montre que s'il y a à choisir.
  lecture.taches = [
    tacheFactice({
      id: TACHE,
      titre: "Créer NOTES.md : le projet décrit en trois lignes",
      run_id: RUN,
    }),
    tacheFactice({ id: AUTRE_TACHE, titre: "Relire NOTES.md", run_id: RUN }),
  ];
});

const monter = (statut: string = EXECUTION_ECHEC, partiel = {}) =>
  rendreAvecEtat(
    <VueRun runId={RUN} />,
    {
      executions: [
        runFactice({
          run_id: RUN,
          objectif: "Ajouter un fichier NOTES.md",
          statut,
          fin: statut === EXECUTION_EN_COURS ? null : "2026-09-27T16:41:02+00:00",
        }),
      ],
      ...partiel,
    },
    projetFactice({ id: "prj-2989d133", nom: "banc-s4-pourquoi" }),
  );

/** La tête du run — la carte qui porte titre, badge, cause et ligne de bilan. */
const tete = () => within(screen.getByRole("region", { name: "Run" }));

/** Monte la vue, ouvre l'onglet Bilan et rend sa région. */
async function ouvrirLeBilan(statut: string = EXECUTION_ECHEC, partiel = {}) {
  const rendu = monter(statut, partiel);
  await userEvent.click(await screen.findByRole("button", { name: /^Bilan/ }));
  const region = within(await screen.findByRole("region", { name: "Bilan du run" }));
  return { ...rendu, region };
}

/* ==================================================================== *
 * ⓪ Ce qui se dit du bilan sans rien dessiner (`lib/bilan`)
 * ==================================================================== */

describe("le bilan, rangé et nommé", () => {
  it("lit le travers d'abord, et tait une rubrique vide", () => {
    // L'ordre de lecture n'est pas celui du backend (qui ouvre sur le livré) :
    // la question de l'écran est « qu'est-ce qui a mal tourné ? ».
    expect(rubriquesDuBilan(bilanEchec()).map((r) => r.cle)).toEqual([
      "echec",
      "recommandation",
      "acte",
      "livre",
    ]);
  });

  it("résume la nature de ce qui a failli, ce qu'il faut changer et les actes", () => {
    expect(resumeDuBilan(bilanEchec())).toBe(
      "ce qui a failli se reproduira · 2 choses à changer · 1 acte sorti ou accordé sans personne",
    );
    const alea = bilanEchec({
      constats: [
        { ...bilanEchec().constats[1], nature: "alea" },
        { ...bilanEchec().constats[1], nature: "inconnue" },
      ],
    });
    // Une nature illisible se range en « indéterminée », comme le backend.
    expect(resumeDuBilan(alea)).toBe("ce qui a failli : aléa (1), nature indéterminée (1)");
    // Deux constats sur la même panne ne font pas « 2 échecs » : la tête ne compte
    // pas des constats comme des échecs, qu'elle dit « 1 échec » juste au-dessus
    // (relevé par le regard neuf, sur un run réel).
    const deux = bilanEchec({
      constats: [bilanEchec().constats[1], bilanEchec().constats[1]],
    });
    expect(resumeDuBilan(deux)).toBe("ce qui a failli se reproduira");
    expect(resumeDuBilan(bilanFactice())).toBe("aucun constat n'a tenu contre ses pièces");
    expect(
      resumeDuBilan(bilanFactice({ constats: [bilanEchec().constats[0]] })),
    ).toBe("1 constat sur pièces, aucun sur ce qui a failli");
  });

  it("lit l'état d'un backend antérieur à #1285, qui ne le disait pas", () => {
    expect(etatDuBilan({ run_id: RUN, bilan: bilanEchec() })).toEqual({
      etat: ETAT_BILAN_RENDU,
      raison: "",
    });
    expect(etatDuBilan({ run_id: RUN, bilan: null })).toEqual({
      etat: ETAT_BILAN_ABSENT,
      raison: "",
    });
  });

  it("rend une pièce d'entrée par sa ligne du journal, une synthèse par son texte", () => {
    const [p2, p8, p9] = bilanEchec().pieces;
    expect([p2, p8, p9].map(estUneLigneDuJournal)).toEqual([false, true, true]);
    // Un bilan d'avant #1285 ne dit pas `synthese` : l'usage n'est jamais une
    // entrée, et une pièce d'entrée n'en cite qu'une (la règle du backend).
    expect(estUneLigneDuJournal({ ...p2, synthese: undefined })).toBe(false);
    expect(estUneLigneDuJournal({ ...p9, synthese: undefined })).toBe(true);
    expect(
      estUneLigneDuJournal({ ...p9, synthese: undefined, entrees: ["j-1", "j-2"] }),
    ).toBe(false);
    // On ne lit au journal que ce qui sera rendu comme une de ses lignes.
    expect(entreesALire(bilanEchec()).sort()).toEqual(["j-0054", "j-0055", "j-0056"]);
  });

  it("se dit en mots au journal du run, jamais par le code du bus", () => {
    // La ligne du journal où les pièces renvoient : « Bilan du run, sur pièces :
    // bilan_rendu » avant #1285 — la branche par défaut rendait le code tel quel.
    const phrase = resumeEvenement(
      evenementDepuisEntree(
        entreeJournalFactice({
          type: "agent.activite",
          run_id: RUN,
          agent: "orchestrateur",
          titre: "Bilan du run, sur pièces",
          statut: STATUT_BILAN_RENDU,
          detail: "7 constats sur pièces, dont 1 sur ce qui a failli.",
        }),
      ),
    );
    expect(phrase).toBe(
      "Bilan du run — 7 constats sur pièces, dont 1 sur ce qui a failli.",
    );
    expect(phrase).not.toContain("bilan_rendu");
  });

  it("rend les pièces d'un constat dans l'ordre où il les cite, sans en inventer", () => {
    const bilan = bilanEchec();
    expect(
      piecesDuConstat(bilan, { pieces: ["P9", "P404", "P2"] }).map((p) => p.id),
    ).toEqual(["P9", "P2"]);
  });
});

/* ==================================================================== *
 * ① La tête dit le bilan en une ligne, et y mène
 * ==================================================================== */

describe("la tête d'un run soldé", () => {
  it("dit le bilan en une ligne, sous la cause, avec la nature de l'échec", async () => {
    monter();

    const ligne = await tete().findByText(/ce qui a failli se reproduira/);
    expect(ligne).toHaveTextContent(
      "· ce qui a failli se reproduira · 2 choses à changer · 1 acte sorti ou accordé sans personne",
    );
    expect(lecture.appelsBilan).toContain(RUN);
  });

  it("mène à l'onglet du bilan, qui porte son compte, puis retire le renvoi", async () => {
    monter();

    await userEvent.click(await tete().findByRole("button", { name: /Lire le bilan/ }));

    expect(
      await screen.findByRole("region", { name: "Bilan du run" }),
    ).toBeInTheDocument();
    const onglet = screen.getByRole("button", { name: "Bilan (5 constats)" });
    expect(onglet).toHaveAttribute("aria-current", "page");
    // Proposer d'aller là où l'on est serait un geste pour rien.
    expect(tete().queryByRole("button", { name: /Lire le bilan/ })).not.toBeInTheDocument();
    // Le pipeline reste la lecture d'ouverture : on y revient d'un onglet.
    await userEvent.click(screen.getByRole("button", { name: "Pipeline" }));
    expect(tete().getByRole("button", { name: /Lire le bilan/ })).toBeInTheDocument();
  });

  it("ne demande rien pour un run en vol, et n'en dit rien", async () => {
    monter(EXECUTION_EN_COURS);

    await screen.findByRole("button", { name: /^Bilan/ });
    expect(lecture.appelsBilan).toEqual([]);
    expect(tete().queryByText(/^Bilan$/)).not.toBeInTheDocument();
    // L'onglet reste là, sans compte — et dit quand le bilan viendra.
    await userEvent.click(screen.getByRole("button", { name: "Bilan" }));
    expect(
      await screen.findByText(/Le bilan se rend à la fin du run/),
    ).toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ② Le travers d'abord, la nature par un mot
 * ==================================================================== */

describe("un run en échec", () => {
  it("rend les rubriques dans l'ordre de lecture, chacune avec ses constats", async () => {
    const { region } = await ouvrirLeBilan();

    const intitules = region
      .getAllByRole("heading", { level: 3 })
      .map((titre) => titre.textContent);
    expect(intitules).toEqual([
      "Ce qui a failli, et pourquoi",
      "Ce qu'il faut changer",
      "Actes sortis du projet ou accordés sans personne",
      "Ce qui a été livré",
    ]);
    expect(region.queryByText("Consommation sans résultat")).not.toBeInTheDocument();
    expect(region.getByText("5 constats sur pièces")).toBeInTheDocument();
  });

  it("porte la nature d'un échec en toutes lettres, et nomme la tâche par son titre", async () => {
    const { region } = await ouvrirLeBilan();

    // docs/30 §3.4 : la couleur ne porte jamais le sens seule — le mot est écrit.
    expect(region.getByText("Se reproduira")).toBeInTheDocument();
    expect(
      region.getAllByText("Tâche : Créer NOTES.md : le projet décrit en trois lignes")[0],
    ).toBeInTheDocument();
    expect(region.queryByText(/Tâche : rediger-notes-md/)).not.toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ③ Chaque constat mène à ses pièces, qui s'ouvrent dans la trace
 * ==================================================================== */

describe("les pièces d'un constat", () => {
  it("se déplient en clair : la ligne du journal, jamais le texte écrit pour le modèle", async () => {
    const { region } = await ouvrirLeBilan();

    await userEvent.click(region.getAllByText("2 pièces")[0]);

    // La ligne du journal telle que le journal la dit (`resumeEvenement`), avec
    // le détail que le moteur a consigné.
    // P9 est citée par deux constats : sa ligne est rendue sous chacun.
    expect(
      (await region.findAllByText(/« Créer NOTES\.md : le projet décrit en trois lignes » a échoué/))[0],
    ).toBeVisible();
    // Le repère « — » d'une tâche jamais exécutée n'est pas un sujet (relevé par
    // le regard neuf : « — a échoué sur … »).
    expect(region.queryByText(/— a échoué/)).not.toBeInTheDocument();
    expect(region.getAllByText(/plafond de tokens dépassé : 36809/)[0]).toBeInTheDocument();
    expect(region.queryByText(/tache\.statut/)).not.toBeInTheDocument();
    // Une synthèse, elle, se dit par son libellé — des phrases, la tâche par son
    // titre —, jamais par le texte que le modèle a lu (identifiant, champs).
    expect(
      region.getByText(/a consommé 0 tokens, coût inconnu, en 0 s — issue : Échec\./),
    ).toBeInTheDocument();
    expect(region.queryByText(/Usage de la tâche/)).not.toBeInTheDocument();
    // Les lignes ont été lues **par leur identifiant**, où qu'elles soient.
    expect(
      lecture.appelsJournal.some((appel) => appel.ids?.includes("j-0055")),
    ).toBe(true);
  });

  it("s'ouvrent dans le journal sur leurs seules entrées, et le journal entier revient", async () => {
    const { region } = await ouvrirLeBilan();
    await userEvent.click(region.getAllByText("2 pièces")[0]);

    await userEvent.click(
      region.getAllByRole("button", { name: "Voir la pièce P9 dans le journal" })[0],
    );

    const journal = await screen.findByRole("region", { name: "Journal du run" });
    expect(screen.getByRole("button", { name: "Journal" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    // Dans la section du journal, sous son titre — la place qu'elle a dans la
    // frise (relevé par le regard neuf).
    expect(within(journal).getByText(/La pièce P9 du bilan · 1 entrée citée/)).toBeInTheDocument();
    await waitFor(() => expect(within(journal).getAllByRole("listitem")).toHaveLength(1));
    expect(lecture.appelsJournal.at(-1)).toEqual({ runId: RUN, ids: ["j-0055"] });

    await userEvent.click(screen.getByRole("button", { name: "Tout le journal" }));
    await waitFor(() => expect(within(journal).getAllByRole("listitem")).toHaveLength(2));
    expect(screen.queryByText(/du bilan ·/)).not.toBeInTheDocument();
  });

  it("s'ouvrent toutes ensemble d'un constat, sans le déplier", async () => {
    const { region } = await ouvrirLeBilan();

    await userEvent.click(region.getAllByRole("button", { name: "Voir dans le journal" })[0]);

    expect(
      await screen.findByText(/Les 2 pièces d'un constat du bilan · 1 entrée citée/),
    ).toBeInTheDocument();
  });

  it("s'ouvrent dans la frise quand elles y figurent, entrée cerclée", async () => {
    const { region } = await ouvrirLeBilan();
    await userEvent.click(region.getAllByText("2 pièces")[0]);

    // P9 porte sur un statut de tâche : la frise le montre. P10 (l'issue du run)
    // n'y est pas, et ne propose pas de l'y ouvrir.
    await userEvent.click(
      (await region.findAllByRole("button", { name: "Voir la pièce P9 dans la frise" }))[0],
    );

    const frise = await screen.findByRole("region", { name: "Frise d'activité du run" });
    expect(within(frise).getByText(/La pièce P9 du bilan · 1 entrée cerclée/)).toBeInTheDocument();
    expect(within(frise).getByText(/Citée au bilan/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Bilan (5 constats)" }));
    const bilan = within(await screen.findByRole("region", { name: "Bilan du run" }));
    // Le constat du livré, qui ferme la lecture, ne cite que P10.
    await userEvent.click(bilan.getAllByRole("button", { name: "1 pièce" }).at(-1)!);
    expect(
      bilan.getByRole("button", { name: "Voir la pièce P10 dans le journal" }),
    ).toBeInTheDocument();
    expect(
      bilan.queryByRole("button", { name: "Voir la pièce P10 dans la frise" }),
    ).not.toBeInTheDocument();
  });

  it("change de lecture par la bascule sans garder l'éclairage", async () => {
    const { region } = await ouvrirLeBilan();
    await userEvent.click(region.getAllByRole("button", { name: "Voir dans le journal" })[0]);
    await screen.findByText(/du bilan ·/);

    await userEvent.click(screen.getByRole("button", { name: "Frise" }));
    await userEvent.click(screen.getByRole("button", { name: "Journal" }));

    // Un onglet choisi à la main montre tout, pas l'éclairage d'un geste passé.
    await screen.findByRole("region", { name: "Journal du run" });
    expect(screen.queryByText(/du bilan ·/)).not.toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ④ Les cinq états du ticket
 * ==================================================================== */

describe("les états du bilan", () => {
  it("un run terminé sans défaut : ni échec, ni rubrique vide", async () => {
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_RENDU,
      bilan: bilanEchec({
        statut: EXECUTION_TERMINEE,
        constats: bilanEchec().constats.filter((c) => c.rubrique !== "echec"),
      }),
    });
    monter(EXECUTION_TERMINEE);

    expect(
      await tete().findByText(/· 2 choses à changer · 1 acte sorti ou accordé sans personne/),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /^Bilan/ }));
    const region = within(await screen.findByRole("region", { name: "Bilan du run" }));
    expect(region.queryByText("Ce qui a failli, et pourquoi")).not.toBeInTheDocument();
    expect(region.queryByText("Se reproduira")).not.toBeInTheDocument();
  });

  it("un bilan en cours de rédaction, run tout juste soldé", async () => {
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_EN_REDACTION,
    });
    const { region } = await ouvrirLeBilan();

    expect(region.getByText(/Bilan en cours de rédaction/)).toBeInTheDocument();
    expect(tete().getByText(/en cours de rédaction, sur les pièces du journal/)).toBeInTheDocument();
    // Pas de compte sur l'onglet : rien n'est encore jugé.
    expect(screen.getByRole("button", { name: "Bilan" })).toBeInTheDocument();
  });

  it.each([
    [RAISON_BILAN_MODELE_MUET, /le modèle n'a pas répondu quand il s'est soldé/],
    [RAISON_BILAN_REPONSE_ILLISIBLE, /la réponse du modèle ne se lisait pas/],
    ["", /il s'est soldé avant que Maestro n'en rende, ou le modèle n'a pas répondu/],
  ])("un bilan absent le dit, et dit pourquoi (raison « %s »)", async (raison, phrase) => {
    lecture.bilan = reponseBilanFactice({ run_id: RUN, etat: ETAT_BILAN_ABSENT, raison });
    monter();

    expect(await tete().findByText(/· aucun pour ce run/)).toBeInTheDocument();
    await userEvent.click(tete().getByRole("button", { name: /Pourquoi/ }));
    const region = within(await screen.findByRole("region", { name: "Bilan du run" }));
    expect(region.getByText(phrase)).toBeInTheDocument();

    // Et renvoie aux pièces qui restent : le journal entier.
    await userEvent.click(region.getByRole("button", { name: "Ouvrir le journal du run" }));
    expect(await screen.findByRole("region", { name: "Journal du run" })).toBeInTheDocument();
    expect(screen.queryByText(/du bilan ·/)).not.toBeInTheDocument();
  });

  it("l'API en erreur : l'onglet dit l'indisponibilité, jamais « pas de bilan »", async () => {
    lecture.echecBilan = ErreurApi.injoignable(`/api/executions/${RUN}/bilan`);
    const { region } = await ouvrirLeBilan();

    expect(await region.findByText(/Bilan indisponible — la lecture a échoué/)).toBeInTheDocument();
    expect(region.queryByText(/n'a pas de bilan/)).not.toBeInTheDocument();
    expect(tete().queryByText(/aucun pour ce run/)).not.toBeInTheDocument();
  });

  it("le shell en panne : la tête garde le bilan déjà lu, comme l'onglet", async () => {
    // Relevé par le regard neuf sur la vraie stack (état injoignable) : la tête
    // taisait le bilan pendant que l'onglet le montrait encore. Le bilan d'un
    // run soldé ne change plus — la tête dit ce que sa lecture a rendu.
    monter(EXECUTION_ECHEC, {
      erreur: ErreurApi.injoignable("/api/executions"),
    });

    expect(await tete().findByText(/ce qui a failli se reproduira/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Bilan (5 constats)" })).toBeInTheDocument();
  });

  it("nomme la tâche par le titre servi avec le bilan, sans attendre la liste des tâches", async () => {
    // Sur la vraie stack, la liste des tâches arrivait après le bilan — et revenait
    // vide pendant l'extinction de l'API : chaque constat retombait sur
    // l'identifiant de sa tâche (relevé par le regard neuf, deux fois).
    lecture.taches = [];
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_RENDU,
      bilan: bilanEchec(),
      taches: {
        [TACHE]: "Créer NOTES.md : le projet décrit en trois lignes",
        [AUTRE_TACHE]: "Relire NOTES.md",
      },
    });
    const { region } = await ouvrirLeBilan();

    expect(region.getAllByText(/Tâche : Créer NOTES\.md/)[0]).toBeInTheDocument();
    expect(region.queryByText(/Tâche : rediger-notes-md/)).not.toBeInTheDocument();
  });

  it("ne renomme pas l'unique tâche d'un run sous chaque constat", async () => {
    // Relevé par le regard neuf : sur un run d'une tâche, la même ligne « Tâche : … »
    // se répétait sous chacun des neuf constats, sans rien apprendre.
    lecture.taches = [lecture.taches[0]];
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_RENDU,
      bilan: bilanEchec(),
      taches: { [TACHE]: "Créer NOTES.md : le projet décrit en trois lignes" },
    });
    const { region } = await ouvrirLeBilan();

    expect(region.queryByText(/^Tâche :/)).not.toBeInTheDocument();
    // Le constat, lui, reste entier.
    expect(region.getByText(/limite de tokens fixée au lancement/)).toBeInTheDocument();
  });

  it("retombe sur l'identifiant quand aucun titre n'est connu, plutôt que de taire la tâche", async () => {
    lecture.taches = [];
    const { region } = await ouvrirLeBilan();

    expect(region.getAllByText("Tâche : rediger-notes-md")[0]).toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ⑤ Ce qui a été écarté reste visible
 * ==================================================================== */

describe("les constats écartés", () => {
  it("restent au pied, repliés, chacun avec sa raison", async () => {
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_RENDU,
      bilan: bilanEchec({
        ecartes: [
          {
            rubrique: "echec",
            texte: "La maquette a manqué de mémoire.",
            pieces: ["P999"],
            raison: "pièce inexistante : P999",
          },
        ],
      }),
    });
    const { region } = await ouvrirLeBilan();

    expect(region.getByText("5 constats sur pièces · 1 écarté")).toBeInTheDocument();
    await userEvent.click(region.getByText("1 constat écarté faute de pièce"));
    expect(region.getByText("La maquette a manqué de mémoire.")).toBeInTheDocument();
    expect(region.getByText("Écarté : pièce inexistante : P999.")).toBeInTheDocument();
    // Jamais montré comme un constat : il n'est dans aucune rubrique.
    const rubriques = region.getAllByRole("list")[0];
    expect(within(rubriques).queryByText("La maquette a manqué de mémoire.")).not.toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ⑥ Sobriété et accessibilité
 * ==================================================================== */

describe("sobriété et accessibilité", () => {
  it("n'ajoute aucun bloc : la lecture vit dans la bascule (docs/30 §4)", async () => {
    const { container, region } = await ouvrirLeBilan();
    await userEvent.click(region.getAllByText("2 pièces")[0]);

    const places = placesDe(container);
    expect(places.anonymes).toEqual([]);
    expect(places.corps).toEqual(["Run", "Bilan du run"]);
    expect(places.corps.length).toBeLessThanOrEqual(BLOCS_MAX);
  });

  it("ne porte aucune violation serious ou critical, pièces dépliées", async () => {
    lecture.bilan = reponseBilanFactice({
      run_id: RUN,
      etat: ETAT_BILAN_RENDU,
      bilan: bilanEchec({
        ecartes: [
          { rubrique: "echec", texte: "Sans pièce.", pieces: [], raison: "aucune pièce citée" },
        ],
      }),
    });
    const { region } = await ouvrirLeBilan();
    await userEvent.click(region.getAllByText("2 pièces")[0]);
    await region.findAllByRole("button", { name: "Voir la pièce P9 dans la frise" });

    const violations = await auditerLaPage();
    expect(bloquantes(violations), `\n${raconter(violations)}\n`).toHaveLength(0);
  });
});

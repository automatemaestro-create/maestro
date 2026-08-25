/**
 * Les régions live des écrans temps réel (#538, lot 6 de #532 — docs/30 §3.3).
 *
 * Ce fichier est **le critère**, pas son accompagnement : le ticket demande « un
 * test qui garde les deux », et c'est le lot précédent (#537, le filet a11y) qui
 * explique pourquoi — une région live posée sans test est exactement l'`aria-live`
 * unique d'avant ce chantier : présente dans le code (`AssistantFlottant`),
 * **absente de l'écran** (sonde du 2026-08-25 : `aria-live` = 0 sur dix écrans,
 * le composant qui le portait n'étant pas déployé au repos).
 *
 * Ce qu'il garde, dans l'ordre :
 *
 * ① **Le vocabulaire, sans DOM** — `lib/annonces` compare deux relevés. C'est là
 *    que vit la promesse « annoncer un état, pas un journal », donc c'est là
 *    qu'elle se vérifie le plus finement : seules les hausses parlent, un
 *    franchissement dit le total, et les deux attentes humaines sont **absentes**
 *    du relevé des runs (elles appartiennent à l'assertive).
 * ② **La présence, écran par écran** — le contrôle que la sonde du ticket a fait
 *    à la main : combien d'éléments portent `aria-live` sur cet écran ? La réponse
 *    attendue est **un** polie, **zéro** assertive (celle-ci vit dans le shell).
 *    Le compte se fait sur l'**attribut** et non sur le rôle : `role="status"`
 *    implique `polite`, mais c'est l'attribut que la sonde mesure, et une
 *    implication n'est pas une mesure.
 * ③ **Le contenu après un événement simulé** — une tâche qui change de colonne,
 *    un run qui se solde, un flux qui avance.
 * ④ **Le débit** — le vrai sujet du ticket. Une rafale ne coûte **qu'une** phrase,
 *    et douze événements se disent « 12 nouveaux événements » plutôt que douze
 *    fois. C'est le seul groupe qui a besoin de fausses minuteries : la fenêtre
 *    d'agrégation est ce qu'on y observe.
 * ⑤ **L'assertive, et sa réserve** — une seule région dans tout le shell, qui ne
 *    parle que d'arbitrages ; et la réciproque, qui compte autant : une tâche
 *    terminée ne la réveille pas, un arbitrage n'est pas redit par la région polie
 *    de l'écran qui le montre.
 *
 * ⚠ `vi.mock("@/lib/api")` ci-dessous **remplace** celui de `tests/setup.ts` :
 * `chargerProjets` et `chargerJournal` y sont reconduits, faute de quoi la porte
 * d'entrée du shell et la page Journal partiraient sur un vrai `fetch`.
 */

import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageCouts from "@/app/couts/page";
import PageJournal from "@/app/journal/page";
import TableauDeBord from "@/app/page";
import PageValidations from "@/app/validations/page";
import { ValidationBriefs } from "@/components/brief/ValidationBriefs";
import { FilChat } from "@/components/FilChat";
import { ListeRuns } from "@/components/runs/ListeRuns";
import { VueRun } from "@/components/runs/VueRun";
import { Shell } from "@/components/Shell";
import {
  mesureDeLaDepense,
  mesuresDesRuns,
  mesuresDesTaches,
  phraseDesChangements,
  resumeArbitrages,
} from "@/lib/annonces";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import { formatCout } from "@/lib/format";
import { marquerGuideVu } from "@/lib/guide";
import {
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
  EXECUTION_TERMINEE,
  VALIDATION_APPROUVEE,
  type PageJournal as PageJournalType,
  type Tache,
} from "@/lib/types";
import { DELAI_ANNONCE_MS } from "@/lib/useAnnonce";

import {
  coutExecutionFactice,
  evenementFactice,
  pageJournalCourante,
  poserEtatGlobal,
  poserProjetActif,
  projetFactice,
  projetsDeclares,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
  usageFactice,
  validationFactice,
} from "./aides";

const lecture = vi.hoisted(() => ({ taches: [] as Tache[] }));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits tels que `tests/setup.ts` les pose : ce mock le **remplace**,
    // et la porte d'entrée du shell (#279) lit cette liste à chaque montage —
    // rendre `[]` la laisserait sur l'écran de déclaration de projet.
    chargerProjets: async () => projetsDeclares(),
    chargerJournal: async (): Promise<PageJournalType> => pageJournalCourante(),
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => null,
  };
});

vi.mock("@/lib/useAnalyticsCouts", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAnalyticsCouts: () => ({
    vue: null,
    connecte: true,
    chargement: true,
    rafraichissement: false,
    erreur: null,
  }),
}));

const PROJET = projetFactice();
const RUN = "3ff0bcb065f9";

/** Une tâche du projet, sur la colonne demandée. */
const tache = (id: string, statut: string): Tache =>
  tacheFactice({ id, titre: `Tâche ${id}`, statut });

/** Le tableau de bord, rendu avec l'état posé — et son re-rendu, à l'identique. */
const monterTableau = (partiel: Parameters<typeof rendreAvecEtat>[1] = {}) => {
  const rendu = rendreAvecEtat(<TableauDeBord />, partiel, PROJET);
  return {
    ...rendu,
    rejouer: () =>
      rendu.rerender(
        <FournisseurEtatGlobal projet={PROJET}>
          <TableauDeBord />
        </FournisseurEtatGlobal>,
      ),
  };
};

/** La région polie d'un écran, désignée par son nom accessible. */
const regionPolie = (libelle: string) =>
  screen.getByRole("status", { name: libelle });

// ------------------------------------- ① Le vocabulaire, sans DOM

describe("ce qu'un relevé dit quand il bouge (lib/annonces)", () => {
  it("n'annonce que les hausses, et les agrège en une phrase", () => {
    // Une tâche qui passe de « en cours » à « terminée » fait baisser une
    // colonne et monter l'autre : annoncer les deux dirait deux fois le même
    // événement, en sens contraires.
    const avant = mesuresDesTaches([tache("T-1", "en_cours"), tache("T-2", "en_cours")]);
    const apres = mesuresDesTaches([tache("T-1", "terminee"), tache("T-2", "terminee")]);

    expect(phraseDesChangements(avant, apres)).toBe("2 tâches terminées.");
  });

  it("se tait quand rien n'a bougé — le cas nominal", () => {
    const releve = mesuresDesTaches([tache("T-1", "en_cours")]);
    expect(phraseDesChangements(releve, releve)).toBeNull();
  });

  it("accorde le libellé sur la hausse, jamais sur le stock", () => {
    const avant = mesuresDesTaches([tache("T-1", "en_cours"), tache("T-2", "terminee")]);
    const apres = mesuresDesTaches([tache("T-1", "terminee"), tache("T-2", "terminee")]);

    // Deux tâches terminées à l'arrivée, **une seule** vient de l'être.
    expect(phraseDesChangements(avant, apres)).toBe("1 tâche terminée.");
  });

  it("dit plusieurs familles dans la même phrase", () => {
    const avant = [
      ...mesuresDesTaches([tache("T-1", "en_cours")]),
      ...mesuresDesRuns([runFactice({ run_id: RUN })]),
    ];
    const apres = [
      ...mesuresDesTaches([tache("T-1", "terminee")]),
      ...mesuresDesRuns([runFactice({ run_id: RUN, statut: EXECUTION_TERMINEE })]),
    ];

    expect(phraseDesChangements(avant, apres)).toBe(
      "1 tâche terminée, 1 run terminé.",
    );
  });

  it("ignore une clé absente du relevé précédent", () => {
    // Un écran qui change de forme (un run choisi, un filtre posé) n'est pas une
    // activité : compter depuis zéro annoncerait tout son contenu comme s'il
    // venait d'arriver.
    const apres = mesuresDesTaches([tache("T-1", "terminee")]);
    expect(phraseDesChangements([], apres)).toBeNull();
  });

  it("ne parle de la dépense qu'au franchissement d'un dollar, et dit le total", () => {
    const centimes = phraseDesChangements(
      [mesureDeLaDepense(0.2)],
      [mesureDeLaDepense(0.9)],
    );
    expect(centimes).toBeNull();

    const franchi = phraseDesChangements(
      [mesureDeLaDepense(0.9)],
      [mesureDeLaDepense(1.2)],
    );
    // Le total et non la hausse : « +0,30 $ » obligerait à se rappeler d'où l'on
    // partait.
    expect(franchi).toBe(`dépense du projet : ${formatCout(1.2)}.`);
  });

  it("laisse les deux attentes humaines hors du relevé des runs", () => {
    // Elles attendent une action, donc elles appartiennent à l'assertive. Les
    // dire ici aussi les dirait deux fois.
    const avant = mesuresDesRuns([]);
    const apres = mesuresDesRuns([
      runFactice({ run_id: "a", statut: EXECUTION_EN_ATTENTE_BRIEF }),
      runFactice({ run_id: "b", statut: EXECUTION_EN_ATTENTE_REPONSES }),
    ]);

    expect(phraseDesChangements(avant, apres)).toBeNull();
  });

  it("nomme les deux familles d'arbitrage séparément", () => {
    // « 3 en attente » obligerait à ouvrir l'écran pour savoir de quoi il
    // retourne : répondre à des questions et approuver une action sensible ne
    // demandent ni la même disponibilité ni la même personne.
    expect(resumeArbitrages(0, 0)).toBeNull();
    expect(resumeArbitrages(1, 0)).toBe("1 validation en attente");
    expect(resumeArbitrages(0, 2)).toBe("2 briefs en attente");
    expect(resumeArbitrages(2, 1)).toBe("2 validations et 1 brief en attente");
  });
});

// ------------------------------------- ② La présence, écran par écran

describe("chaque écran temps réel porte sa région polie, et une seule", () => {
  const ECRANS: { nom: string; libelle: string; monter: () => void }[] = [
    {
      nom: "le tableau de bord",
      libelle: "Activité du tableau de bord",
      monter: () => monterTableau({ taches: [tache("T-1", "en_cours")] }),
    },
    {
      nom: "la liste des runs",
      libelle: "Activité des runs",
      monter: () =>
        rendreAvecEtat(<ListeRuns />, { executions: [runFactice()] }, PROJET),
    },
    {
      nom: "la vue d'un run",
      libelle: "Activité du run",
      monter: () =>
        rendreAvecEtat(
          <VueRun runId={RUN} />,
          { executions: [runFactice({ run_id: RUN })] },
          PROJET,
        ),
    },
    {
      nom: "les coûts",
      libelle: "Dépense du projet",
      monter: () => rendreAvecEtat(<PageCouts />, {}, PROJET),
    },
    {
      nom: "les validations",
      libelle: "Arbitrages tranchés",
      monter: () =>
        rendreAvecEtat(
          <PageValidations />,
          { validations: [validationFactice()] },
          PROJET,
        ),
    },
    {
      nom: "le journal",
      libelle: "Activité du journal",
      monter: () =>
        rendreAvecEtat(<PageJournal />, { evenements: [evenementFactice()] }, PROJET),
    },
    {
      nom: "les briefs",
      libelle: "Activité des briefs",
      monter: () =>
        rendreAvecEtat(
          <ValidationBriefs />,
          {
            executions: [
              runFactice({ run_id: RUN, statut: EXECUTION_EN_ATTENTE_BRIEF }),
            ],
          },
          PROJET,
        ),
    },
    {
      nom: "le fil d'un agent",
      libelle: "Activité du fil avec dev",
      monter: () => rendreAvecEtat(<FilChat agent="dev" />, {}, PROJET),
    },
  ];

  for (const { nom, libelle, monter } of ECRANS) {
    it(`${nom} annonce ses changements, sans couper la parole`, async () => {
      monter();

      const region = await screen.findByRole("status", { name: libelle });
      expect(region).toHaveAttribute("aria-live", "polite");
      // Entière ou pas du tout : sans `aria-atomic`, un lecteur d'écran peut ne
      // dire que le fragment qui a changé — un mot sorti de sa phrase.
      expect(region).toHaveAttribute("aria-atomic", "true");

      // Le compte de la sonde du ticket, sur l'attribut : une région polie, et
      // aucune assertive — celle-ci vit dans le shell, une seule fois.
      expect(document.querySelectorAll('[aria-live="polite"]')).toHaveLength(1);
      expect(document.querySelectorAll('[aria-live="assertive"]')).toHaveLength(0);
    });
  }
});

// ------------------------------------- ③ Le contenu après un événement simulé

describe("ce qui est annoncé après un événement", () => {
  it("ne dit rien à l'ouverture de l'écran", async () => {
    // Arriver sur un écran n'est pas un changement d'état, et le lecteur d'écran
    // est déjà en train de lire la page.
    monterTableau({ taches: [tache("T-1", "en_cours"), tache("T-2", "terminee")] });

    const region = await screen.findByRole("status", {
      name: "Activité du tableau de bord",
    });
    expect(region.textContent).toBe("");
  });

  it("annonce une tâche qui change de colonne", async () => {
    const { rejouer } = monterTableau({ taches: [tache("T-1", "en_cours")] });

    poserEtatGlobal({ taches: [tache("T-1", "terminee")], revision: 1 });
    rejouer();

    await waitFor(() =>
      expect(regionPolie("Activité du tableau de bord")).toHaveTextContent(
        "1 tâche terminée.",
      ),
    );
  });

  it("annonce un run terminé et la dépense qui franchit un dollar", async () => {
    const { rejouer } = monterTableau({
      taches: [tache("T-1", "en_cours")],
      executions: [runFactice({ run_id: RUN })],
      couts: [coutExecutionFactice({ total: usageFactice({ cout_usd: 0.9 }) })],
    });

    poserEtatGlobal({
      taches: [tache("T-1", "en_cours")],
      executions: [runFactice({ run_id: RUN, statut: EXECUTION_TERMINEE })],
      couts: [coutExecutionFactice({ total: usageFactice({ cout_usd: 1.2 }) })],
      revision: 1,
    });
    rejouer();

    // Comparé sur `textContent` et non par `toHaveTextContent` : un montant
    // formaté porte une **espace insécable étroite** avant son symbole, que la
    // normalisation d'espaces du matcher réduit d'un côté seulement — les deux
    // chaînes s'affichent alors identiques dans un diff qui refuse de conclure.
    await waitFor(() =>
      expect(regionPolie("Activité du tableau de bord").textContent).toBe(
        `1 run terminé, dépense du projet : ${formatCout(1.2)}.`,
      ),
    );
  });

  it("annonce une validation tranchée, sur l'écran qui la montre", async () => {
    const rendu = rendreAvecEtat(
      <PageValidations />,
      { validations: [validationFactice({ tache_id: "T-1" })] },
      PROJET,
    );

    poserEtatGlobal({
      validations: [
        validationFactice({ tache_id: "T-1", statut: VALIDATION_APPROUVEE }),
      ],
      revision: 1,
    });
    rendu.rerender(
      <FournisseurEtatGlobal projet={PROJET}>
        <PageValidations />
      </FournisseurEtatGlobal>,
    );

    await waitFor(() =>
      expect(regionPolie("Arbitrages tranchés")).toHaveTextContent(
        "1 validation tranchée.",
      ),
    );
  });
});

// ------------------------------------- ④ Le débit : une rafale, une phrase

describe("le débit du flux ne devient pas le débit des annonces", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("dit la rafale en une phrase, et non une phrase par événement", () => {
    // Les rechargements sont coalescés à 150 ms et le flux plafonné à
    // `MAX_EVENEMENTS` : branchée naïvement, une région live parlerait plusieurs
    // fois par seconde. Ici, trois tâches terminées coup sur coup coûtent deux
    // phrases — celle du front avant, puis **une seule** pour tout le reste de
    // la fenêtre.
    const { rejouer } = monterTableau({
      taches: [
        tache("T-1", "en_cours"),
        tache("T-2", "en_cours"),
        tache("T-3", "en_cours"),
      ],
    });
    const region = regionPolie("Activité du tableau de bord");

    const poser = (terminees: number, revision: number) => {
      poserEtatGlobal({
        taches: [
          tache("T-1", terminees >= 1 ? "terminee" : "en_cours"),
          tache("T-2", terminees >= 2 ? "terminee" : "en_cours"),
          tache("T-3", terminees >= 3 ? "terminee" : "en_cours"),
        ],
        revision,
      });
      act(() => rejouer());
    };

    // Front avant : un changement isolé s'annonce tout de suite.
    poser(1, 1);
    expect(region).toHaveTextContent("1 tâche terminée.");

    // Ce qui suit tombe dans la fenêtre : rien de plus n'est dit pendant.
    poser(2, 2);
    poser(3, 3);
    expect(region).toHaveTextContent("1 tâche terminée.");

    // Au bout de la fenêtre, les deux tâches restantes tiennent en une phrase —
    // et non « 1 tâche terminée » deux fois de plus.
    act(() => {
      vi.advanceTimersByTime(DELAI_ANNONCE_MS);
    });
    expect(region).toHaveTextContent("2 tâches terminées.");
  });

  it("fait réentendre une phrase qui se répète", () => {
    // Le piège des régions live : une région parle sur **mutation**, pas sur
    // affectation. Réécrire la même chaîne ne touche pas le DOM, donc ne
    // s'annonce pas — et « 1 tâche terminée » deux fenêtres d'affilée est un cas
    // courant, pas une curiosité. C'est ce que la clé de `lib/useAnnonce` règle,
    // et elle ne se voit qu'ici : le texte, lui, est identique des deux côtés.
    const { rejouer } = monterTableau({
      taches: [tache("T-1", "en_cours"), tache("T-2", "en_cours")],
    });
    const region = regionPolie("Activité du tableau de bord");

    poserEtatGlobal({
      taches: [tache("T-1", "terminee"), tache("T-2", "en_cours")],
      revision: 1,
    });
    act(() => rejouer());
    const premier = region.firstElementChild;
    expect(premier).toHaveTextContent("1 tâche terminée.");

    // La fenêtre se referme, puis la seconde tâche se termine : même phrase.
    act(() => {
      vi.advanceTimersByTime(DELAI_ANNONCE_MS);
    });
    poserEtatGlobal({
      taches: [tache("T-1", "terminee"), tache("T-2", "terminee")],
      revision: 2,
    });
    act(() => rejouer());

    expect(region.firstElementChild).toHaveTextContent("1 tâche terminée.");
    expect(region.firstElementChild).not.toBe(premier);
  });

  it("dit douze événements du journal en une ligne", async () => {
    // L'écran dont le code portait « pas de région live : le flux temps réel
    // ferait de ce compteur un bavard permanent ». Il l'est en effet à condition
    // d'annoncer chaque ligne ; agrégé, il tient en une phrase.
    const rendu = rendreAvecEtat(<PageJournal />, { evenements: [] }, PROJET);
    // L'historique persisté est lu au montage, et la région n'apparaît qu'une
    // fois cette lecture close : on laisse la promesse se résoudre au lieu
    // d'attendre, `waitFor` s'appuyant sur les minuteries qu'on vient de figer.
    await act(async () => {});
    const region = regionPolie("Activité du journal");

    poserEtatGlobal({
      evenements: Array.from({ length: 12 }, (_, i) =>
        evenementFactice({
          tache_id: `T-${i}`,
          horodatage: `2026-07-28T10:00:${String(i).padStart(2, "0")}Z`,
        }),
      ),
      revision: 1,
    });
    act(() =>
      rendu.rerender(
        <FournisseurEtatGlobal projet={PROJET}>
          <PageJournal />
        </FournisseurEtatGlobal>,
      ),
    );

    expect(region).toHaveTextContent("12 nouveaux événements.");
  });
});

// ------------------------------------- ⑤ L'assertive, et sa réserve

describe("la région assertive du shell", () => {
  const monterShell = async () => {
    const rendu = render(
      <Shell>
        <p>contenu de la page</p>
      </Shell>,
    );
    await screen.findByText("contenu de la page");
    return {
      ...rendu,
      rejouer: () =>
        rendu.rerender(
          <Shell>
            <p>contenu de la page</p>
          </Shell>,
        ),
    };
  };

  const regionAssertive = () =>
    screen.getByRole("alert", { name: "Demandes d'arbitrage" });

  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
  });

  it("est unique dans toute l'application", async () => {
    // Une demande d'arbitrage doit s'entendre quel que soit l'écran ouvert : la
    // monter par écran l'aurait rendue muette sur les autres, ou dite N fois par
    // un écran qui en porterait plusieurs.
    await monterShell();

    expect(document.querySelectorAll('[aria-live="assertive"]')).toHaveLength(1);
    expect(regionAssertive()).toHaveAttribute("aria-live", "assertive");
    expect(regionAssertive()).toHaveAttribute("aria-atomic", "true");
  });

  it("annonce une demande d'arbitrage qui arrive", async () => {
    poserEtatGlobal({ validations: [] });
    const { rejouer } = await monterShell();
    expect(regionAssertive().textContent).toBe("");

    poserEtatGlobal({ validations: [validationFactice()], revision: 1 });
    rejouer();

    await waitFor(() =>
      expect(regionAssertive()).toHaveTextContent(
        "Arbitrage requis : 1 validation en attente.",
      ),
    );
  });

  it("compte les briefs suspendus avec les validations", async () => {
    poserEtatGlobal({ validations: [], executions: [] });
    const { rejouer } = await monterShell();

    poserEtatGlobal({
      validations: [validationFactice()],
      executions: [
        runFactice({ run_id: RUN, statut: EXECUTION_EN_ATTENTE_BRIEF }),
      ],
      revision: 1,
    });
    rejouer();

    await waitFor(() =>
      expect(regionAssertive()).toHaveTextContent(
        "Arbitrage requis : 1 validation et 1 brief en attente.",
      ),
    );
  });

  it("ne coupe pas la parole pour une tâche terminée", async () => {
    // La réserve est le second critère du ticket : l'assertive interrompt, donc
    // elle ne parle que de ce qui attend une action.
    poserEtatGlobal({ taches: [tache("T-1", "en_cours")] });
    const { rejouer } = await monterShell();

    poserEtatGlobal({ taches: [tache("T-1", "terminee")], revision: 1 });
    rejouer();

    await waitFor(() => expect(regionAssertive().textContent).toBe(""));
  });

  it("garde les arbitrages hors de la région polie de l'écran", async () => {
    // La réciproque : les dire des deux côtés les dirait deux fois, une fois en
    // coupant la parole.
    const { rejouer } = monterTableau({
      taches: [tache("T-1", "en_cours")],
      validations: [],
    });

    poserEtatGlobal({
      taches: [tache("T-1", "en_cours")],
      validations: [validationFactice()],
      revision: 1,
    });
    rejouer();

    // La demande est bien arrivée à l'écran — sans quoi le contrôle ci-dessous
    // constaterait le silence d'un écran où il ne s'est rien passé.
    await screen.findByText("Publier la version 1.2");
    expect(regionPolie("Activité du tableau de bord").textContent).toBe("");
  });
});

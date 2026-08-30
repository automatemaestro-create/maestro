/**
 * L'onglet **Logs** d'une fiche agent (#266, lot 14 de #243).
 *
 * Le lot a livré sans tests (docs/10 §5.1) ; ce fichier les rattrape. Ce qu'il
 * garde n'est pas « l'onglet s'affiche » mais les quatre décisions qui font que
 * ce qu'on y lit est vrai :
 *
 * ① **l'appartenance à l'agent vient de l'API** — le filtre `agent` est passé à
 *    `GET /api/journal`, jamais appliqué après coup sur le fil du shell. C'est le
 *    point qui ne se négocie pas : une page de journal est plafonnée, donc
 *    refiltrer une page du *projet entier* ne montrerait d'un agent discret que le
 *    silence des autres. Il ne s'observe qu'en regardant **ce qui est demandé** —
 *    le contenu rendu vient de `poserJournal` quoi qu'il arrive (même dessin que
 *    `porteesDemandees` et `canauxDemandes`) ;
 * ② **le groupement par tâche**, le seul vrai ajout de rendu du lot : un fil par
 *    tâche, dans l'ordre du fil, et ce qui ne relève d'aucune tâche dans un groupe
 *    « Hors tâche » qui prend sa place au lieu d'être relégué ou tu ;
 * ③ **le filtre par niveau**, dont l'ordre est celui de `NIVEAUX_LOG` et non
 *    l'alphabet — c'est tout l'arbitrage du ticket : le niveau est la *famille*
 *    d'une ligne, et « qu'est-ce qu'on lui a refusé ? » doit s'isoler d'un choix ;
 * ④ **trois silences qui ne se confondent pas** — la lecture en vol, l'agent qui
 *    n'a rien fait, et le filtre qui ne rend rien. Les fondre ferait chercher une
 *    panne là où il n'y a qu'un filtre trop étroit.
 *
 * L'onglet est monté par `ContenuOngletAgent` et non en important son composant :
 * c'est le point d'entrée que la fiche utilise, et il reste vrai si le composant
 * change de fichier. Le réseau est débranché ici même (mock local de `@/lib/api`).
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { NIVEAUX_LOG } from "@/lib/evenements";
import { GROUPE_HORS_TACHE } from "@/lib/journal";

import {
  entreeJournalFactice,
  evenementFactice,
  pageJournalCourante,
  poserJournal,
  projetFactice,
  rendreAvecEtat,
} from "./aides";

/**
 * Les filtres passés à `chargerJournal` depuis le début du test.
 *
 * Le mock de `setup.ts` rend la page sans regarder ses arguments — ce qui suffit
 * partout ailleurs, et ne suffit pas ici : la promesse du lot est *ce qu'on
 * demande*, pas ce qu'on reçoit. Un mock local prend le pas sur celui du setup,
 * à condition d'en reconduire les autres routes (piège documenté dans
 * `agents.test.tsx`).
 */
const filtresDemandes: { portee: string; agent?: string }[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const aides = await import("./aides");
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(aides.projetsDeclares()),
    chargerFournisseurs: () => Promise.resolve(aides.fournisseursDuPoste()),
    chargerCatalogue: () => Promise.resolve(aides.catalogueAgents()),
    chargerJournal: (portee: string, filtres: { agent?: string } = {}) => {
      filtresDemandes.push({ portee, agent: filtres.agent });
      return Promise.resolve(aides.pageJournalCourante());
    },
  };
});

const PROJET = projetFactice({ nom: "Dépensio" });

/** Une ligne de journal de `dev`, sur la tâche nommée. */
function ligne(partiel: Parameters<typeof entreeJournalFactice>[0] = {}) {
  return entreeJournalFactice({ agent: "dev", run_id: "run-1", ...partiel });
}

/** Monte l'onglet Logs de `dev` et attend la fin de la première lecture. */
async function monter() {
  const utilisateur = userEvent.setup();
  const vue = rendreAvecEtat(
    <ContenuOngletAgent nom="dev" onglet="logs" />,
    {},
    PROJET,
  );
  await waitFor(() =>
    expect(screen.queryByText(/Lecture du journal/)).not.toBeInTheDocument(),
  );
  return { utilisateur, ...vue };
}

/** Les libellés proposés par une liste de filtre, l'option « tout » comprise. */
function optionsDe(libelle: string): string[] {
  return within(screen.getByLabelText(libelle))
    .getAllByRole("option")
    .map((option) => option.textContent ?? "");
}

/**
 * Les groupes de tâches affichés, dans l'ordre.
 *
 * Cherchés **dans** la section « Logs de dev » et non sur l'écran entier : la
 * carte des filtres est une région elle aussi, et la ramasser ferait passer un
 * ordre de groupes pour faux alors qu'il est juste.
 */
function groupesAffiches(): string[] {
  return within(screen.getByRole("region", { name: "Logs de dev" }))
    .getAllByRole("region")
    .map((region) => region.getAttribute("aria-label") ?? "");
}

beforeEach(() => {
  filtresDemandes.length = 0;
});

describe("① l'appartenance à l'agent vient de l'API", () => {
  it("demande le journal du seul agent de la fiche", async () => {
    poserJournal([ligne({ id: "j-1", titre: "Écrire les tests" })]);

    await monter();

    // Le filtre part avec la requête : refiltrer une page du projet entier ne
    // montrerait d'un agent discret que le silence des autres.
    expect(filtresDemandes.at(-1)?.agent).toBe("dev");
  });

  it("ne mêle au direct que les lignes de cet agent", async () => {
    poserJournal([]);

    await rendreAvecEtat(
      <ContenuOngletAgent nom="dev" onglet="logs" />,
      {
        evenements: [
          evenementFactice({ agent: "dev", tache_id: "T-1", titre: "À moi" }),
          evenementFactice({ agent: "qa", tache_id: "T-9", titre: "Pas à moi" }),
        ],
      },
      PROJET,
    );

    // Le fil du shell couvre tout le projet : c'est le seul endroit où le tri par
    // agent se fait côté écran, et il le peut — c'est ce qui vient d'arriver, pas
    // une page bornée sur laquelle on chercherait.
    await screen.findByRole("region", { name: "À moi" });
    expect(screen.queryByRole("region", { name: "Pas à moi" })).toBeNull();
  });

  it("demande un autre journal sur la fiche d'un autre agent", async () => {
    poserJournal([]);
    await monter();

    rendreAvecEtat(<ContenuOngletAgent nom="qa" onglet="logs" />, {}, PROJET);
    await waitFor(() => expect(filtresDemandes.at(-1)?.agent).toBe("qa"));

    // Le filtre suit la fiche, il n'est pas figé au montage : deux onglets Logs
    // ne peuvent pas se retrouver à lire le même journal.
    expect(filtresDemandes.map(({ agent }) => agent)).toContain("dev");
  });

  it("lit dans la portée du projet actif, jamais au-delà", async () => {
    poserJournal([]);

    await monter();

    // Même règle que tout écran depuis #281 : ce qu'on lit est borné au projet
    // de la fenêtre, et l'agent n'est qu'un second filtre par-dessus.
    expect(filtresDemandes.at(-1)?.portee).toContain(PROJET.id);
  });
});

describe("② les lignes se rangent par tâche", () => {
  it("ouvre un fil par tâche, dans l'ordre du fil", async () => {
    poserJournal([
      ligne({ id: "j-1", tache_id: "T-2", titre: "Relire la doc" }),
      ligne({ id: "j-2", tache_id: "T-1", titre: "Écrire les tests" }),
      ligne({ id: "j-3", tache_id: "T-1", titre: "Écrire les tests" }),
    ]);

    await monter();

    // La tâche dont la ligne la plus récente est la plus récente ouvre la liste :
    // celle sur laquelle l'agent travaille en ce moment.
    expect(groupesAffiches()).toEqual(["Relire la doc", "Écrire les tests"]);
  });

  it("recueille ce qui ne relève d'aucune tâche sans le reléguer", async () => {
    poserJournal([
      ligne({ id: "j-1", tache_id: "", titre: "" }),
      ligne({ id: "j-2", tache_id: "T-1", titre: "Écrire les tests" }),
    ]);

    await monter();

    // Planification, capacité, proposition de playbook : ni relégué en fin de
    // liste, ni tu — le groupe prend sa place dans le même ordre.
    expect(groupesAffiches()).toEqual([GROUPE_HORS_TACHE, "Écrire les tests"]);
  });

  it("nomme la tâche par son titre, à défaut par son identifiant", async () => {
    poserJournal([ligne({ id: "j-1", tache_id: "T-42", titre: "" })]);

    await monter();

    await screen.findByRole("region", { name: "T-42" });
  });
});

describe("③ le filtre par niveau isole une famille", () => {
  it("ne propose que les niveaux présents, dans l'ordre du plus pressant", async () => {
    poserJournal([
      ligne({ id: "j-1", statut: "en_cours", tache_id: "T-1", titre: "Tests" }),
      ligne({ id: "j-2", statut: "refus_outil", tache_id: "T-1", titre: "Tests" }),
      ligne({ id: "j-3", statut: "echec", tache_id: "T-1", titre: "Tests" }),
    ]);

    await monter();

    // Dérivés du fil (aucune option morte), mais **non triés par libellé** :
    // l'alphabet mettrait « Décision » avant « Erreur » et perdrait la seule
    // chose que cet ordre dit.
    expect(optionsDe("Niveau")).toEqual([
      "Tous les niveaux",
      "Erreur",
      "Refus",
      "Info",
    ]);
    expect(NIVEAUX_LOG.map(({ libelle }) => libelle)).toEqual([
      "Erreur",
      "Refus",
      "Décision",
      "Info",
    ]);
  });

  it("isole les refus d'un seul choix", async () => {
    poserJournal([
      ligne({ id: "j-1", statut: "en_cours", tache_id: "T-1", titre: "Tests" }),
      ligne({
        id: "j-2",
        statut: "refus_outil",
        tache_id: "T-2",
        titre: "Déployer",
      }),
    ]);
    const { utilisateur } = await monter();

    await utilisateur.selectOptions(screen.getByLabelText("Niveau"), "refus");

    // « Qu'est-ce qu'on lui a interdit ? » est la question qu'on pose le plus
    // souvent à un journal d'agent : aucune échelle de sévérité ne l'isolerait.
    expect(screen.queryByRole("region", { name: "Tests" })).toBeNull();
    await screen.findByRole("region", { name: "Déployer" });
    expect(screen.getByText("1 ligne(s) sur 2")).toBeInTheDocument();
  });

  it("croise le niveau et la tâche, et se réinitialise d'un geste", async () => {
    poserJournal([
      ligne({ id: "j-1", statut: "en_cours", tache_id: "T-1", titre: "Tests" }),
      ligne({ id: "j-2", statut: "echec", tache_id: "T-2", titre: "Déployer" }),
    ]);
    const { utilisateur } = await monter();

    await utilisateur.selectOptions(screen.getByLabelText("Tâche"), "T-1");
    await utilisateur.selectOptions(screen.getByLabelText("Niveau"), "erreur");

    // Les deux filtres se croisent : plus rien ne correspond, et c'est distinct
    // d'un agent qui n'a rien fait.
    await screen.findByText("Aucune ligne ne correspond à ces filtres.");

    await utilisateur.click(
      screen.getByRole("button", { name: "Réinitialiser les filtres" }),
    );

    expect(screen.getByText("2 ligne(s)")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Réinitialiser les filtres" }),
    ).toBeNull();
  });

  it("propose les tâches du fil et non une liste écrite en dur", async () => {
    poserJournal([
      ligne({ id: "j-1", tache_id: "T-1", titre: "Écrire les tests" }),
      ligne({ id: "j-2", tache_id: "T-2", titre: "Relire la doc" }),
      ligne({ id: "j-3", tache_id: "", titre: "" }),
    ]);

    await monter();

    // Les tâches se trient par nom (là où les niveaux gardent leur ordre), et
    // le groupe **hors tâche** n'est pas une option : il n'a pas d'identifiant à
    // passer en filtre. Il est bien affiché pour autant — le rendu et la liste
    // déroulante ne disent pas la même chose, et c'est voulu.
    expect(optionsDe("Tâche")).toEqual([
      "Toutes les tâches",
      "Écrire les tests",
      "Relire la doc",
    ]);
    expect(groupesAffiches()).toContain(GROUPE_HORS_TACHE);
  });
});

describe("④ trois silences qui ne se confondent pas", () => {
  it("dit que la première lecture est en vol", async () => {
    poserJournal([]);
    // Une lecture qui ne se résout pas : c'est l'état intermédiaire, invisible
    // autrement — un « rien encore » affiché ici serait faux la moitié du temps.
    const enVol = vi
      .spyOn(await import("@/lib/api"), "chargerJournal")
      .mockReturnValue(new Promise(() => {}));

    rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="logs" />, {}, PROJET);

    await screen.findByText("Lecture du journal de dev…");
    enVol.mockRestore();
  });

  it("distingue l'agent qui n'a rien fait d'un filtre trop étroit", async () => {
    poserJournal([]);

    await monter();

    // Le silence de l'agent, nommé avec son projet : ni une panne (le bandeau
    // le dirait), ni un onglet cassé.
    expect(
      screen.getByText(/Rien encore : aucune ligne de dev/),
    ).toHaveTextContent("Dépensio");
    expect(screen.queryByText(/Aucune ligne ne correspond/)).toBeNull();
  });

  it("garde les lignes lisibles quand le flux temps réel est coupé", async () => {
    poserJournal([ligne({ id: "j-1", tache_id: "T-1", titre: "Tests" })]);

    rendreAvecEtat(
      <ContenuOngletAgent nom="dev" onglet="logs" />,
      { connecte: false },
      PROJET,
    );

    // Ce qui s'arrête est l'ajout des lignes suivantes ; l'historique, lui, est là.
    await screen.findByText(/Flux temps réel interrompu/);
    await screen.findByRole("region", { name: "Tests" });
  });

  it("dit qu'il ne montre qu'une partie d'un journal plafonné", async () => {
    poserJournal([ligne({ id: "j-1", tache_id: "T-1", titre: "Tests" })]);
    const page = pageJournalCourante();
    vi.spyOn(await import("@/lib/api"), "chargerJournal").mockResolvedValue({
      ...page,
      total: 500,
    });

    await monter();

    // `total` est le compte avant pagination : taire l'écart ferait lire une
    // page pour un journal entier.
    expect(screen.getByText(/1 plus récentes des 500 lignes/)).toBeInTheDocument();
  });
});

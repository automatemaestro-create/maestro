/**
 * Les décisions qu'un agent a tranchées **seul**, dans la vue d'un run (#1026,
 * tests différés au lot final de #1019 → #1027 ; docs/05 §6.18).
 *
 * Le parent pose une condition à l'autonomie — *elle n'est acceptable que si
 * elle se vérifie après coup*. Le lot 2 (#1024) a fait consigner ces décisions ;
 * sans cet écran elles étaient **écrites puis invisibles**, noyées dans le
 * journal et rendues par la branche `default` de `resumeEvenement`, qui affichait
 * le statut brut du bus. Ce que ces tests gardent est donc ce qui ferait
 * retomber la vue dans ce défaut sans rien casser :
 *
 * ① **une ligne par décision, dans l'ordre servi.** Le tri appartient au backend
 *    (du plus récent au plus ancien, départagé par le rang du journal) : un
 *    second tri ici finirait par contredire le premier ;
 *
 * ② **l'hypothèse se distingue par une forme et un mot, jamais par une teinte.**
 *    C'est docs/30 §3.4 et `a11y.test.tsx` : la couleur ne porte jamais le sens
 *    seule. C'est aussi la moitié la plus facile à perdre — une icône se
 *    remplace, un badge se « simplifie » ;
 *
 * ③ **rien n'est redécoupé.** `decision` et `raison` viennent de deux champs que
 *    le moteur sépare à l'écriture, et l'agent se lit `agent · Rôle` comme dans
 *    le nœud du pipeline et sur la carte du Kanban — le même agent doit se lire
 *    pareil d'une vue à l'autre (relevé par le regard neuf, #980) ;
 *
 * ④ **les comptes viennent du backend.** `total` et `hypotheses` comptent avant
 *    le plafond : les recompter sur ce qui a été reçu donnerait faux dès que la
 *    liste est tronquée ;
 *
 * ⑤ **« rien n'a été décidé seul » est une réponse**, pas une absence de vue.
 *    L'onglet reste là, et l'écran dit *pourquoi* c'est vide — comme il dit
 *    autre chose quand il charge, et autre chose encore quand la lecture a
 *    échoué : trois états, trois phrases, parce qu'un « aucune décision » servi
 *    sur une API éteinte serait un mensonge.
 *
 * Réseau débranché comme partout (`tests/setup.ts`) : `chargerDecisionsExecution`
 * est mocké ici, la vue est la vraie.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { VueRun } from "@/components/runs/VueRun";
import {
  ORIGINE_HYPOTHESE,
  type DecisionsRun,
  type PageJournal,
} from "@/lib/types";

import {
  decisionFactice,
  decisionsRunFactice,
  grapheFactice,
  pageJournalCourante,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  tacheFactice,
} from "./aides";

const RUN = "3ff0bcb065f9";

/** Ce que la fausse lecture rendra, et avec quel run on l'a appelée. */
const lecture = vi.hoisted(() => ({
  decisions: null as DecisionsRun | null,
  appels: [] as string[],
  echec: null as Error | null,
  // Les tâches **du run** : la vue les charge elle-même (`lib/useTachesRun`),
  // elles ne viennent pas de l'état global — c'est cette liste-là qui permet à
  // une ligne d'ouvrir le détail de la tâche qu'elle désigne.
  taches: [] as unknown[],
}));

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits : ce mock **remplace** celui de `tests/setup.ts`.
    chargerProjets: async () => [],
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    chargerTaches: async () => lecture.taches,
    chargerGrapheExecution: async () => grapheFactice({ run_id: RUN }),
    chargerDecisionsExecution: async (runId: string) => {
      lecture.appels.push(runId);
      if (lecture.echec !== null) throw lecture.echec;
      return lecture.decisions;
    },
  };
});

beforeEach(() => {
  lecture.decisions = decisionsRunFactice({ run_id: RUN });
  lecture.appels.length = 0;
  lecture.echec = null;
  lecture.taches = [];
});

const monter = (partiel = {}) =>
  rendreAvecEtat(
    <VueRun runId={RUN} />,
    {
      executions: [runFactice({ run_id: RUN, objectif: "Prototyper un mini-CRM" })],
      ...partiel,
    },
    projetFactice({ id: "prj-7f3a1c2b", nom: "Dépensio" }),
  );

/** Monte la vue du run, ouvre l'onglet Décisions et rend sa région. */
async function decisions(partiel = {}) {
  monter(partiel);
  await userEvent.click(screen.getByRole("button", { name: "Décisions" }));
  return within(
    await screen.findByRole("region", { name: "Décisions autonomes du run" }),
  );
}

/* ==================================================================== *
 * ① Une ligne par décision, dans l'ordre servi
 * ==================================================================== */

describe("la liste des décisions", () => {
  it("rend une ligne par décision, dans l'ordre que le backend a servi", async () => {
    // Le tri (instant décroissant, départagé par le rang du journal) vit côté
    // serveur. Un second tri ici finirait par contredire le premier — d'où une
    // liste servie dans un ordre que la vue rend telle quelle.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [
        decisionFactice({ id: "j-0031", decision: "servie en premier" }),
        decisionFactice({ id: "j-0004", decision: "servie en second" }),
      ],
    });
    const vue = await decisions();

    const lignes = vue.getAllByRole("listitem");
    expect(lignes).toHaveLength(2);
    expect(lignes[0]).toHaveTextContent("servie en premier");
    expect(lignes[1]).toHaveTextContent("servie en second");
    // La lecture est demandée **pour ce run**, sans portée de projet : le run
    // seul suffit à désigner ce qu'on lit.
    expect(lecture.appels).toContain(RUN);
  });

  it("porte la décision, sa raison, l'agent avec son rôle et l'heure", async () => {
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [
        decisionFactice({
          decision: "Pagination en curseur plutôt qu'en offset",
          raison: "la liste est triée par date et l'offset dérive",
          agent: "developpeur",
          role: "Développeur",
          horodatage: "2026-09-20T17:04:11+00:00",
        }),
      ],
    });
    const vue = await decisions();

    const ligne = vue.getAllByRole("listitem")[0];
    // Les deux champs que le moteur sépare à l'écriture (#1024), rendus tels
    // quels : la vue ne redécoupe rien.
    expect(ligne).toHaveTextContent("Pagination en curseur plutôt qu'en offset");
    expect(ligne).toHaveTextContent("la liste est triée par date et l'offset dérive");
    // `agent · Rôle`, comme le nœud du pipeline et la carte du Kanban : le même
    // agent doit se lire pareil d'une vue à l'autre (#980). L'identifiant seul
    // est le nom technique, pas le nom lisible.
    expect(ligne).toHaveTextContent("developpeur · Développeur");
    // L'heure : une `<time>` machine-lisible, doublée de la date absolue — « il
    // y a 4 min » ne dit pas **de quand**.
    const instant = ligne.querySelector("time");
    expect(instant).toHaveAttribute("dateTime", "2026-09-20T17:04:11+00:00");
    expect(instant?.getAttribute("title")).not.toBe("");
  });

  it("n'invente pas de séparateur quand le flux ne porte pas de rôle", async () => {
    // Le rôle **tombe** plutôt que de laisser un « · » qui ne sépare rien : une
    // entrée d'un producteur qui n'en porte pas reste lisible.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [decisionFactice({ agent: "developpeur", role: "" })],
    });
    const vue = await decisions();

    expect(vue.getAllByRole("listitem")[0]).not.toHaveTextContent("developpeur ·");
  });
});

/* ==================================================================== *
 * ② L'hypothèse : une forme ET un mot, jamais une teinte
 * ==================================================================== */

describe("les deux familles", () => {
  it("marque une hypothèse par le mot « Hypothèse » et une icône dédiée", async () => {
    // docs/30 §3.4 : la couleur ne porte jamais le sens seule. Le mot est donc
    // **écrit**, et l'icône qui l'accompagne est décorative — c'est pourquoi le
    // test cherche le mot d'abord, et la forme ensuite.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [
        decisionFactice({
          id: "j-0031",
          origine: ORIGINE_HYPOTHESE,
          hypothese: true,
          decision: "je pars sur SQLite",
          raison: "aucune réponse après 240 s à : Postgres ou SQLite ?",
        }),
        decisionFactice({ id: "j-0012", decision: "tranchée sans demander" }),
      ],
    });
    const vue = await decisions();

    const [hypothese, tranchee] = vue.getAllByRole("listitem");
    expect(hypothese).toHaveTextContent("Hypothèse");
    expect(hypothese).toHaveTextContent("je pars sur SQLite");
    // Et la ligne dit **pourquoi** elle en est une : la question posée, et la
    // borne écoulée.
    expect(hypothese).toHaveTextContent("aucune réponse après 240 s");
    // La décision tranchée seule, elle, ne porte pas la marque : les deux
    // familles ne se mélangent jamais — « je n'avais pas à demander » et « j'ai
    // demandé et personne n'a répondu » ne sont pas le même fait.
    expect(tranchee).not.toHaveTextContent("Hypothèse");

    // Les deux gouttières portent une icône, et elles diffèrent : sans cela, la
    // distinction ne tiendrait qu'au badge.
    const glyphe = (ligne: HTMLElement) =>
      ligne.querySelector("svg")?.innerHTML ?? "";
    expect(glyphe(hypothese)).not.toBe("");
    expect(glyphe(hypothese)).not.toBe(glyphe(tranchee));
  });
});

/* ==================================================================== *
 * ③ Le compte, la troncature, et le renvoi vers la tâche
 * ==================================================================== */

describe("ce que la vue dit autour de la liste", () => {
  it("compte les décisions et dit combien ont été prises faute de réponse", async () => {
    // Les deux chiffres viennent du backend et comptent **avant** le plafond :
    // les recompter sur `entrees` donnerait faux dès que la liste est tronquée.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [decisionFactice()],
      total: 7,
      hypotheses: 2,
    });
    const vue = await decisions();

    expect(vue.getByText(/7 décisions/)).toHaveTextContent("dont 2 sans réponse");
  });

  it("ne compte rien quand il n'y a rien à compter", async () => {
    // Un « 0 décision » à côté d'un état vide qui dit déjà la même chose serait
    // la dire deux fois.
    const vue = await decisions();

    expect(vue.queryByText(/décision/)).not.toHaveTextContent(/^0 /);
  });

  it("dit que la liste est bornée, et où lire le reste", async () => {
    // Une borne **muette** ferait passer un run bavard pour un run sobre.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [decisionFactice()],
      total: 240,
      plafond: 200,
      tronquee: true,
    });
    const vue = await decisions();

    expect(vue.getByText(/200 décisions les plus récentes sur 240/)).toBeInTheDocument();
    expect(vue.getByText(/journal du run/)).toBeInTheDocument();
  });

  it("renvoie vers la tâche par son titre, et ouvre son détail sur place", async () => {
    // Le renvoi ouvre le panneau de détail comme une carte du Kanban : c'est le
    // seul « lien vers la tâche » de ce produit — il n'y a pas de route par
    // tâche —, et en inventer une ici en ferait deux.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [decisionFactice({ tache_id: "api-crud", tache: "API CRUD" })],
    });
    lecture.taches = [tacheFactice({ id: "api-crud", run_id: RUN, titre: "API CRUD" })];
    const vue = await decisions();

    const renvoi = vue.getByRole("button", { name: "API CRUD" });
    // Le nom complet reste récupérable à la souris même si le texte est tronqué
    // à l'œil (relevé par le regard neuf, #980).
    expect(renvoi).toHaveAttribute("title", "API CRUD");

    await userEvent.click(renvoi);
    expect(await screen.findByRole("dialog")).toHaveTextContent("API CRUD");
  });

  it("retombe sur l'identifiant quand le titre de la tâche n'est pas connu", async () => {
    // Perdre une décision faute de connaître le titre de sa tâche serait
    // l'inverse de ce que cette vue existe pour faire : l'identifiant reste un
    // renvoi valable.
    lecture.decisions = decisionsRunFactice({
      run_id: RUN,
      entrees: [decisionFactice({ tache_id: "api-crud", tache: "" })],
    });
    const vue = await decisions();

    expect(vue.getByRole("button", { name: "api-crud" })).toBeInTheDocument();
  });
});

/* ==================================================================== *
 * ④ Les trois états d'une lecture vide
 * ==================================================================== */

describe("quand la liste est vide", () => {
  it("dit pourquoi un run n'a rien décidé seul, sans retirer l'onglet", async () => {
    // « Rien n'a été décidé seul » est une **réponse**, pas une absence de vue :
    // un onglet qui apparaîtrait en cours de run déplacerait les autres sous le
    // pointeur.
    const vue = await decisions();

    expect(vue.getByText(/Aucune décision tranchée seule/)).toHaveTextContent(
      /aucune question n'est restée sans réponse/,
    );
    expect(screen.getByRole("button", { name: "Décisions" })).toBeInTheDocument();
  });

  it("distingue une lecture en échec d'un run sobre", async () => {
    // Servir « aucune décision » sur une API éteinte serait un mensonge, et le
    // plus coûteux de cette vue : elle existe précisément pour qu'on puisse
    // vérifier après coup.
    lecture.echec = new Error("API injoignable");
    const vue = await decisions();

    await waitFor(() =>
      expect(vue.getByText(/Décisions indisponibles/)).toBeInTheDocument(),
    );
    expect(vue.queryByText(/Aucune décision tranchée seule/)).not.toBeInTheDocument();
  });
});

/**
 * Le projet actif comme **cadre de tous les écrans** (#281, lot 5 de #276).
 *
 * Le lot 1 (#277) a rendu l'API capable de filtrer, le lot 3 (#279) a posé le
 * projet actif : ce lot-ci les branche l'un sur l'autre. Ce qu'un test peut en
 * tenir se range en trois familles, une par critère :
 *
 * 1. **la portée demandée** — la promesse ne se lit pas dans ce qu'un écran
 *    affiche (les données sont factices, elles diraient ce qu'on veut) mais dans
 *    ce qu'il **demande**. `setup.ts` note donc la portée reçue par
 *    `useControlTower`, et c'est elle qu'on regarde. Corollaire vérifié ici : on
 *    ne voit jamais passer `tous`, la vue transverse d'avant ce lot ;
 * 2. **le changement de projet** — ce qui doit disparaître n'est pas seulement
 *    l'état temps réel (un rechargement y suffirait), c'est aussi ce que les
 *    **pages** tiennent : un filtre du Journal posé sur une tâche de l'ancien
 *    projet est exactement le « compteur figé » du critère. D'où la clé de
 *    remontage du shell, et d'où ce test qui la protège — sans elle l'écran
 *    aurait l'air juste, avec un filtre survivant qui ne correspond plus à rien ;
 * 3. **le vide nommé** — trois vides se ressemblent (panne, absence de projet,
 *    rien sur ce projet) et se soignent différemment. Chaque écran doit dire
 *    lequel est le sien, et il ne peut le faire qu'en **nommant** le projet.
 *
 * Les tests Python et la doc de la vague restent différés au lot 6 (#282) ; la
 * décision « ce qui est filtré, ce qui reste global » est, elle, consignée dans
 * docs/05 §2.3 comme le demandait le ticket.
 */

import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PageCouts from "@/app/couts/page";
import PageJournal from "@/app/journal/page";
import TableauDeBord from "@/app/page";
import PageValidations from "@/app/validations/page";
import { Kanban } from "@/components/Kanban";
import { Shell } from "@/components/Shell";
import { PORTEE_TOUS, urlEvenements } from "@/lib/api";
import { marquerGuideVu } from "@/lib/guide";
import { ecrireProjetActifId } from "@/lib/projetActif";

import {
  agentFactice,
  evenementFactice,
  porteesDemandees,
  poserChemin,
  poserProjets,
  projetFactice,
  rendreAvecEtat,
  tacheFactice,
  validationFactice,
} from "./aides";

/** La portée que la page Coûts a passée à ses agrégats — l'autre lecture cadrée. */
const analytics = vi.hoisted(() => ({ portees: [] as string[] }));

vi.mock("@/lib/useAnalyticsCouts", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAnalyticsCouts: (_periode: unknown, portee: string) => {
    analytics.portees.push(portee);
    return {
      vue: {
        portee,
        projet: portee,
        total: {
          appels: 0,
          tokens_entree: 0,
          tokens_sortie: 0,
          tokens_total: 0,
          cout_usd: null,
        },
        serie: [],
        pas: "heure",
        agents: [],
        taches: [],
        executions: [],
      },
      connecte: true,
      chargement: false,
      rafraichissement: false,
      erreur: null,
    };
  },
}));

const DEPENSIO = projetFactice({ id: "prj-depensio", nom: "Dépensio" });
const AUTRE = projetFactice({ id: "prj-autre", nom: "Autre chantier" });

beforeEach(() => {
  analytics.portees.length = 0;
  // Sans cela la visite guidée s'ouvrirait par-dessus le shell une fois entré.
  marquerGuideVu();
});

/** Monte le shell entier, entré dans `projet`, sur la page passée. */
async function entrerDans(projet = DEPENSIO, page = <p>page</p>) {
  poserProjets([DEPENSIO, AUTRE]);
  ecrireProjetActifId(projet.id);
  const rendu = render(<Shell>{page}</Shell>);
  // La résolution du projet actif est différée d'un tick (#279) : rien du cadre
  // n'existe avant, pas même la barre supérieure.
  await screen.findByRole("navigation", { name: "Navigation principale" });
  return rendu;
}

describe("la portée demandée par les écrans", () => {
  it("est celle du projet actif, jamais la vue transverse", async () => {
    await entrerDans();

    expect(porteesDemandees.length).toBeGreaterThan(0);
    expect(new Set(porteesDemandees)).toEqual(new Set([DEPENSIO.id]));
    // `tous` était le défaut d'avant ce lot : le voir passer signerait un appel
    // resté sans cadre — ce que le compilateur interdit désormais (`lib/api`),
    // et que ce test garde côté comportement.
    expect(porteesDemandees).not.toContain(PORTEE_TOUS);
  });

  it("cadre aussi le flux temps réel, à l'ouverture de la socket", () => {
    // Le tri se fait à l'entrée de la file côté backend (#277) : la portée
    // voyage donc dans l'URL, pas dans un filtre client qui laisserait les
    // événements des autres projets arriver puis être écartés.
    expect(urlEvenements("prj-depensio")).toContain("projet=prj-depensio");
    expect(urlEvenements("prj-depensio")).toMatch(/^ws/);
  });

  it("cadre les agrégats de la page Coûts sur le même projet", async () => {
    poserChemin("/couts");
    await entrerDans(DEPENSIO, <PageCouts />);

    expect(analytics.portees).toContain(DEPENSIO.id);
    expect(analytics.portees).not.toContain(PORTEE_TOUS);
  });
});

describe("changer de projet", () => {
  it("relit sur le nouveau et ne laisse rien de l'ancien", async () => {
    const utilisateur = userEvent.setup();
    poserChemin("/journal");
    await entrerDans(
      DEPENSIO,
      <PageJournal />,
    );
    // Un filtre posé, c'est-à-dire de l'état que la **page** tient et qu'aucun
    // rechargement de données ne remettrait à zéro.
    const recherche = screen.getByRole("searchbox", { name: /Rechercher/ });
    await utilisateur.type(recherche, "dev");
    expect(recherche).toHaveValue("dev");
    // L'infobulle du coût cumulé nomme elle aussi le projet depuis #536 (c'était
    // un `title`, donc hors du texte) : on écarte les bulles, sinon la
    // recherche en ramène deux et ne dit plus rien de la page.
    expect(
      screen.getByText(/sur Dépensio/, {
        ignore: "script, style, [role='tooltip']",
      }),
    ).toBeInTheDocument();

    // La bascule passe par le stockage — le chemin qu'emprunteront le sélecteur
    // du lot 4 (#280) comme un autre onglet.
    act(() => ecrireProjetActifId(AUTRE.id));

    expect(
      await screen.findByText(/Rien encore sur Autre chantier/),
    ).toBeInTheDocument();
    // La lecture repart sur le nouveau projet…
    expect(porteesDemandees.at(-1)).toBe(AUTRE.id);
    // …et l'écran aussi : le filtre de l'ancien projet ne survit pas, sans quoi
    // la page resterait posée sur une tâche qui n'existe plus ici.
    expect(
      screen.getByRole("searchbox", { name: /Rechercher/ }),
    ).toHaveValue("");
  });
});

describe("un écran vide dit de quel projet il est vide", () => {
  it("sur le tableau de bord, sans se confondre avec une panne", () => {
    rendreAvecEtat(<TableauDeBord />, {}, DEPENSIO);

    const poste = screen.getByRole("region", { name: "Poste de pilotage vide" });
    expect(poste).toHaveTextContent("Rien encore sur Dépensio");
    // Ni une panne (l'API répond, et sa bannière n'est pas là)…
    expect(screen.queryByRole("alert")).toBeNull();
    // …ni l'absence de projet, que la garde du shell traite avant d'arriver ici.
    expect(poste).toHaveTextContent(/n'est ni une panne/);
    // Et le piège que ce lot rend possible : un run sans projet n'apparaît nulle
    // part, ce qui se cherche longtemps quand l'écran ne le dit pas.
    expect(poste).toHaveTextContent(/sans projet/);
  });

  it("sur le Kanban, quand le reste de l'écran vit", () => {
    render(
      <Kanban
        taches={[]}
        agents={[]}
        reassigner={async () => {}}
        projet={DEPENSIO}
      />,
    );
    expect(screen.getByText(/Rien encore sur Dépensio/)).toBeInTheDocument();
  });

  it("sur le Journal, distinctement d'un filtre sans résultat", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: [] }, DEPENSIO);
    // `findBy` : depuis #478 le vide n'est prononcé qu'une fois la lecture du
    // journal persisté revenue (l'écran dit « Lecture du journal… » avant).
    expect(await screen.findByText(/Rien encore sur Dépensio/)).toBeInTheDocument();

    // Avec des événements, mais un filtre qui n'en retient aucun, la phrase
    // change : ce n'est plus le projet qui est vide, c'est la question posée.
    rendreAvecEtat(
      <PageJournal />,
      { evenements: [evenementFactice({ agent: "dev" })] },
      DEPENSIO,
    );
    const recherches = screen.getAllByRole("searchbox", { name: /Rechercher/ });
    await utilisateur.type(recherches[recherches.length - 1], "introuvable");
    expect(
      screen.getByText(/Aucun événement ne correspond à ces filtres/),
    ).toBeInTheDocument();
  });

  it("sur les Validations, en séparant « rien » de « rien en attente »", () => {
    rendreAvecEtat(<PageValidations />, { validations: [] }, DEPENSIO);
    expect(screen.getByText(/Rien encore sur Dépensio/)).toBeInTheDocument();

    // Des arbitrages ont eu lieu, aucun n'attend : ce n'est pas le même vide,
    // et l'historique en dessous le prouve.
    rendreAvecEtat(
      <PageValidations />,
      { validations: [validationFactice({ statut: "approuvee" })] },
      DEPENSIO,
    );
    expect(
      screen.getByText(/Aucune validation en attente sur Dépensio/),
    ).toBeInTheDocument();
  });

  it("sur les Coûts, où un zéro seul ne dit pas de quoi il est le zéro", async () => {
    poserChemin("/couts");
    await entrerDans(DEPENSIO, <PageCouts />);
    expect(
      screen.getByText(/Rien encore sur Dépensio/),
    ).toBeInTheDocument();
  });
});

describe("ce qui reste global, et le dit", () => {
  it("compte les agents du poste à part de ceux qui travaillent ici", () => {
    // Décision du lot (docs/05 §2.3) : le parc d'agents est une ressource du
    // **poste** — le backend ne le filtre pas, et le filtrer n'aurait pas de
    // sens. Ce qui est cadré, c'est ce que la tuile en dit.
    rendreAvecEtat(
      <TableauDeBord />,
      {
        taches: [tacheFactice({ id: "T-1", agent: "dev", statut: "en_cours" })],
        agents: [
          // Occupé sur une tâche **de ce projet** : il compte en tête.
          agentFactice({
            nom: "dev",
            statut: "occupe",
            taches_en_cours: ["T-1"],
          }),
          // Occupé sur une tâche qu'on ne voit pas d'ici : il passe au détail.
          agentFactice({
            nom: "qa",
            statut: "occupe",
            taches_en_cours: ["T-ailleurs"],
          }),
        ],
      },
      DEPENSIO,
    );
    const tuile = screen.getByText("Agents").closest("article") as HTMLElement;
    expect(tuile).toHaveTextContent("1 sur ce projet");
    expect(tuile).toHaveTextContent("2 agent(s) du poste");
    expect(tuile).toHaveTextContent("1 occupé(s) ailleurs");
  });
});

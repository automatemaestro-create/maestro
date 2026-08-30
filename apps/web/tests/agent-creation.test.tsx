/**
 * La **création d'un agent**, jusqu'au bout (#254 et #257, lots 2 et 5 de #243).
 *
 * `tests/agents.test.tsx` garde déjà le **cadre** de l'écran plein écran : sa
 * route, la porte en tête de liste, la sortie par Échap, la garde du brouillon et
 * le refus du nom que la route occupe. Ce qu'il ne fait jamais, c'est **créer** :
 * `creerAgent` n'y est appelé nulle part, et l'assistant de #257 y lève à dessein.
 * Ce fichier ferme les deux trous.
 *
 * ① **la création aboutit et mène à la fiche née** — le formulaire compose la
 *    définition (nom, rôle, compétences en liste, playbook, réglages), l'envoie,
 *    puis la navigation ouvre l'agent sur son profil. C'est le seul endroit où la
 *    chaîne « écran → API → fiche » se vérifie d'un bout à l'autre ;
 * ② **l'assistant remplit sans rien enregistrer** (#257) — trois promesses qui se
 *    défont facilement : il remplit les champs *à partir de l'intention*, ce qu'il
 *    pose reste **modifiable mot à mot**, et il **ne touche à rien quand il
 *    échoue**. La quatrième, l'abandon, rend au formulaire ce qu'il portait avant
 *    — pas ce qu'un essai précédent y avait mis.
 *
 * Le réseau est débranché ici même (mock local de `@/lib/api`) : ce fichier a
 * besoin d'espionner `creerAgent` et `genererDefinitionAgent`, que le mock du
 * setup ne porte pas.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CreationAgentEcran } from "@/components/CreationAgentEcran";
import type { DefinitionAgent, DefinitionAgentProposee } from "@/lib/types";

import { navigations, poserFournisseurs } from "./aides";

/** Les créations demandées, dans l'ordre. */
const creations: { nom: string; definition: DefinitionAgent }[] = [];
/** Les intentions soumises à l'assistant. */
const intentions: string[] = [];
/** Ce que l'assistant rendra, ou l'échec posé ici. */
let proposition: DefinitionAgentProposee | Error;
/** Ce que la création fera : réussir, ou refuser avec ce motif. */
let refusCreation: string | null = null;

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const aides = await import("./aides");
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(aides.projetsDeclares()),
    chargerJournal: () => Promise.resolve(aides.pageJournalCourante()),
    chargerFournisseurs: () => Promise.resolve(aides.fournisseursDuPoste()),
    chargerCatalogue: () => Promise.resolve(aides.catalogueAgents()),
    creerAgent: (nom: string, definition: DefinitionAgent) => {
      creations.push({ nom, definition });
      return refusCreation === null
        ? Promise.resolve()
        : Promise.reject(new Error(refusCreation));
    },
    genererDefinitionAgent: (intention: string) => {
      intentions.push(intention);
      return proposition instanceof Error
        ? Promise.reject(proposition)
        : Promise.resolve(proposition);
    },
  };
});

/** Monte l'écran de création. */
function monter() {
  return { utilisateur: userEvent.setup(), ...render(<CreationAgentEcran />) };
}

/** Remplit le minimum qu'exige la création : nom, rôle, une compétence, playbook. */
async function remplir(utilisateur: ReturnType<typeof userEvent.setup>) {
  await utilisateur.type(screen.getByRole("textbox", { name: /^Nom/ }), "dev-front");
  await utilisateur.type(screen.getByRole("textbox", { name: /^Rôle/ }), "Développeur front");
  await utilisateur.type(
    screen.getByLabelText(/^Compétences/),
    "react{Enter}typescript{Enter}",
  );
  await utilisateur.type(
    screen.getByRole("textbox", { name: /^Playbook/ }),
    "Tu écris les écrans.",
  );
}

beforeEach(() => {
  creations.length = 0;
  intentions.length = 0;
  refusCreation = null;
  proposition = {
    intention: "",
    nom: "relecteur-sql",
    role: "Relecteur SQL",
    competences: ["sql", "migrations"],
    playbook: "Tu relis les migrations avant application.",
    fournisseur: null,
    modele: null,
  };
});

describe("① la création aboutit et mène à la fiche née", () => {
  it("n'ouvre le bouton qu'une fois la définition complète", async () => {
    const { utilisateur } = monter();
    const creer = screen.getByRole("button", { name: "Créer l'agent" });
    expect(creer).toBeDisabled();

    await utilisateur.type(screen.getByRole("textbox", { name: /^Nom/ }), "dev-front");
    expect(creer).toBeDisabled(); // un nom ne fait pas une définition

    await remplir(utilisateur);

    expect(creer).toBeEnabled();
  });

  it("compose la définition, l'envoie, puis ouvre la fiche sur son profil", async () => {
    const { utilisateur } = monter();
    await remplir(utilisateur);

    await utilisateur.click(screen.getByRole("button", { name: "Créer l'agent" }));

    await waitFor(() => expect(creations).toHaveLength(1));
    expect(creations[0].nom).toBe("dev-front");
    // Les compétences sont une **liste** depuis #256 : c'est la saisie qui a
    // changé de forme, pas le contrat d'API.
    expect(creations[0].definition).toEqual({
      role: "Développeur front",
      competences: ["react", "typescript"],
      playbook: "Tu écris les écrans.",
      modele: null,
      fournisseur: null,
      effort: null,
    });
    // Un agent né sans fiche ouverte serait un agent qu'on a créé à l'aveugle.
    expect(navigations.at(-1)).toBe("/agents/dev-front/profil");
  });

  it("rend « rien de choisi » en défaut légitime et non en trou", async () => {
    const { utilisateur } = monter();
    await remplir(utilisateur);

    await utilisateur.click(screen.getByRole("button", { name: "Créer l'agent" }));

    await waitFor(() => expect(creations).toHaveLength(1));
    // Les trois réglages laissés vides valent « suit le fournisseur et le modèle
    // de l'exécution », et les listes de #255 l'offrent explicitement.
    const { modele, fournisseur, effort } = creations[0].definition;
    expect([modele, fournisseur, effort]).toEqual([null, null, null]);
  });

  it("porte les réglages choisis jusqu'à la définition envoyée", async () => {
    poserFournisseurs({
      fournisseurs: [
        {
          nom: "claude",
          modeles: [
            { nom: "claude-opus-5", libelle: "Opus 5", efforts: ["high", "xhigh"] },
          ],
          modeles_libres: true,
          supporte: true,
          present_ici: true,
          utilisable_ici: true,
          modeles_ici: [],
          constats: [],
        },
      ],
      hors_registre: [],
      incertitudes: [],
    });
    const { utilisateur } = monter();
    await remplir(utilisateur);
    await waitFor(() =>
      expect(
        within(screen.getByLabelText(/^Fournisseur/)).getAllByRole("option").length,
      ).toBeGreaterThan(1),
    );

    await utilisateur.selectOptions(screen.getByLabelText(/^Fournisseur/), "claude");
    // Le modèle est un champ **libre** ici — `modeles_libres` vaut vrai chez
    // Claude, dont la gamme propose sans interdire (#255) : c'est un `<input>`
    // à `<datalist>`, pas un `<select>`.
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-opus-5");
    await utilisateur.selectOptions(screen.getByLabelText(/^Effort/), "xhigh");
    await utilisateur.click(screen.getByRole("button", { name: "Créer l'agent" }));

    await waitFor(() => expect(creations).toHaveLength(1));
    expect(creations[0].definition).toMatchObject({
      fournisseur: "claude",
      modele: "claude-opus-5",
      effort: "xhigh",
    });
  });

  it("garde la saisie et dit pourquoi quand l'API refuse", async () => {
    refusCreation = "nom d'agent réservé : qa";
    const { utilisateur } = monter();
    await remplir(utilisateur);

    await utilisateur.click(screen.getByRole("button", { name: "Créer l'agent" }));

    const alerte = await screen.findByRole("alert");
    expect(alerte).toHaveTextContent("nom d'agent réservé : qa");
    // Rien n'a été créé, donc rien à ouvrir : rester sur la saisie est la seule
    // conduite qui ne perde pas le travail.
    expect(navigations).toHaveLength(0);
    expect(screen.getByRole("textbox", { name: /^Nom/ })).toHaveValue("dev-front");
  });
});

describe("② l'assistant remplit le formulaire sans rien enregistrer", () => {
  it("n'accepte de partir que d'une intention écrite", async () => {
    const { utilisateur } = monter();
    const generer = screen.getByRole("button", { name: "Générer" });
    expect(generer).toBeDisabled();

    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent qui relit mes migrations SQL",
    );

    expect(generer).toBeEnabled();
  });

  it("pose la proposition dans les champs, et le dit", async () => {
    const { utilisateur } = monter();
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent qui relit mes migrations SQL",
    );

    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));

    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /^Nom/ })).toHaveValue(
        "relecteur-sql",
      ),
    );
    expect(intentions).toEqual(["Un agent qui relit mes migrations SQL"]);
    expect(screen.getByRole("textbox", { name: /^Rôle/ })).toHaveValue("Relecteur SQL");
    expect(screen.getByRole("textbox", { name: /^Playbook/ })).toHaveValue(
      "Tu relis les migrations avant application.",
    );
    // Rien n'est enregistré tant que l'agent n'est pas créé — et c'est écrit.
    expect(screen.getByRole("status")).toHaveTextContent(
      /Proposition en brouillon/,
    );
    expect(creations).toHaveLength(0);
  });

  it("laisse corriger mot à mot ce qu'il a proposé", async () => {
    const { utilisateur } = monter();
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent SQL",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /^Rôle/ })).toHaveValue(
        "Relecteur SQL",
      ),
    );

    await utilisateur.clear(screen.getByRole("textbox", { name: /^Rôle/ }));
    await utilisateur.type(
      screen.getByRole("textbox", { name: /^Rôle/ }),
      "Relecteur de migrations",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Créer l'agent" }));

    await waitFor(() => expect(creations).toHaveLength(1));
    // La proposition est un brouillon : ce qui part est ce qu'on a relu.
    expect(creations[0].definition.role).toBe("Relecteur de migrations");
    expect(creations[0].definition.competences).toEqual(["sql", "migrations"]);
  });

  it("rend au formulaire ce qu'il portait avant la proposition", async () => {
    const { utilisateur } = monter();
    await utilisateur.type(screen.getByRole("textbox", { name: /^Nom/ }), "le-mien");
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent SQL",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));
    await waitFor(() =>
      expect(screen.getByRole("textbox", { name: /^Nom/ })).toHaveValue(
        "relecteur-sql",
      ),
    );

    await utilisateur.click(
      screen.getByRole("button", { name: "Abandonner la proposition" }),
    );

    // La saisie d'origine — pas celle d'un essai précédent : l'état d'avant est
    // mémorisé une fois, pas à chaque génération.
    expect(screen.getByRole("textbox", { name: /^Nom/ })).toHaveValue("le-mien");
    // L'intention, elle, reste : c'est le point de départ, pas la proposition.
    expect(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
    ).toHaveValue("Un agent SQL");
  });

  it("propose de régénérer une fois qu'une proposition est en place", async () => {
    const { utilisateur } = monter();
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent SQL",
    );
    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));

    await screen.findByRole("button", { name: "Régénérer" });
    expect(screen.queryByRole("button", { name: "Générer" })).toBeNull();
  });

  it("ne touche à rien quand il échoue", async () => {
    proposition = new Error("quota épuisé");
    const { utilisateur } = monter();
    await utilisateur.type(screen.getByRole("textbox", { name: /^Nom/ }), "le-mien");
    await utilisateur.type(
      screen.getByRole("textbox", { name: /Décrire l'agent en une phrase/ }),
      "Un agent SQL",
    );

    await utilisateur.click(screen.getByRole("button", { name: "Générer" }));

    const alerte = await screen.findByRole("alert");
    expect(alerte).toHaveTextContent("quota épuisé");
    expect(alerte).toHaveTextContent("le formulaire est intact");
    expect(screen.getByRole("textbox", { name: /^Nom/ })).toHaveValue("le-mien");
    expect(screen.getByRole("textbox", { name: /^Rôle/ })).toHaveValue("");
  });
});

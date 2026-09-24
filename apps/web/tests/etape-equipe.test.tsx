/**
 * **L'équipe se corrige avec ses mots, à l'étape d'équipe** (#1159).
 *
 * L'étape ne permettait que de décocher, et la raison d'un rôle écarté promettait
 * « vous pouvez l'ajouter à la validation » sans aucun contrôle pour le faire. La
 * forme retenue (variante A, consignée sur le ticket) : un champ au pied de la
 * liste, dont ce qu'on dit **atterrit sur l'équipe**. Ce filet garde ce qui part
 * et ce qui s'affiche — le modèle, lui, a sa suite côté moteur
 * (`tests/test_equipe_composition.py`) :
 *
 * ① **« ajoute quelqu'un pour la sécurité »** ajoute une ligne retenue, signalée
 *    par un mot, et la réponse du modèle se lit ; le rôle ajouté repart à la
 *    création comme les autres ;
 * ② **la demande porte l'équipe montrée** — cases décochées comprises ;
 * ③ **une demande non comprise** laisse la liste intacte et le texte dans le
 *    champ, pour qu'on reformule ; une panne se dit, sans rien perdre ;
 * ④ **aucun texte ne promet un geste absent** : le repli des écartés renvoie au
 *    champ ;
 * ⑤ **l'écran dit qui a composé l'équipe** quand ce sont les règles, et la ligne
 *    de l'analyse ne contredit plus le compte après une demande ;
 * ⑥ les `**…**` d'une raison servie se rendent en gras.
 *
 * ⚠ Aucune géométrie ni aucun jugement de rendu ici (#308) : c'est la relecture
 * visuelle de la clôture qui regarde l'écran.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { avecGras, EtapeEquipe } from "@/components/projets/EtapeEquipe";
import { appliquerCorrection } from "@/lib/equipe";
import type {
  CorrectionEquipe,
  Projet,
  PropositionEquipe,
  RoleEquipe,
} from "@/lib/types";

const proposerEquipe = vi.fn();
const corrigerEquipe = vi.fn();
const creerEquipe = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    proposerEquipe: (...args: unknown[]) => proposerEquipe(...args),
    corrigerEquipe: (...args: unknown[]) => corrigerEquipe(...args),
    creerEquipe: (...args: unknown[]) => creerEquipe(...args),
  };
});

const PROJET = {
  id: "prj-7f3a1c2b",
  nom: "Pousse",
} as Projet;

function role(partiel: Partial<RoleEquipe> = {}): RoleEquipe {
  return {
    nom: "dev",
    role: "Développeur backend",
    gabarit: "developpeur",
    competences: ["backend"],
    raison: "vous décrivez un service API en Python",
    justification: null,
    instances: 1,
    raison_instances: "une instance",
    outils: ["Read", "Write", "Bash"],
    playbook: "Tu écris le code de Pousse.",
    playbook_origine: "genere",
    playbook_raison: "écrit pour ce projet",
    intention: "",
    skills: [],
    autorisations: [
      {
        outil: "Bash",
        cran: "ask",
        decideur: "auto",
        portee: "projet",
        raison: "il exécute **sans vous demander** dans le projet",
      },
    ],
    politique: { allow: [], ask: { Bash: "auto" }, deny: [] },
    ...partiel,
  };
}

const PROPOSITION: PropositionEquipe = {
  proposition: 1,
  id: "equ-42",
  projet_id: PROJET.id,
  faite_le: "2026-09-24T10:00:00+00:00",
  resume: "Développeur backend, QA — 2 rôle(s), 2 instance(s) ; 2 rôle(s) écarté(s)",
  source: { type: "choix" },
  composition: { origine: "modele", raison: "" },
  roles: [
    role(),
    role({
      nom: "tests",
      role: "QA / Testeur",
      gabarit: "qa",
      raison: "vous avez choisi pytest",
    }),
  ],
  ecartes: [
    { nom: "orchestrateur", role: "Orchestrateur", raison: "c'est Maestro" },
    {
      nom: "donnees",
      role: "Base de données",
      raison: "le service ne stocke rien d'après vos réponses",
    },
  ],
  instances_total: 2,
  cree: false,
  validation: "requise",
};

const SECURITE = role({
  nom: "securite",
  role: "Sécurité applicative",
  gabarit: "",
  competences: ["securite", "audit"],
  raison: "vous demandez quelqu'un pour la sécurité",
  playbook: "# Sécurité applicative\n\nTu audites les dépendances.",
});

function correction(partiel: Partial<CorrectionEquipe> = {}): CorrectionEquipe {
  return {
    reponse: "",
    ajouts: [],
    retraits: [],
    remis: [],
    instances: {},
    cree: false,
    ...partiel,
  };
}

async function etapeChargee() {
  render(<EtapeEquipe projet={PROJET} onTermine={() => {}} />);
  await screen.findByText("Développeur backend");
}

async function demander(texte: string) {
  const champ = screen.getByRole("textbox", { name: /Il manque quelqu'un/ });
  await userEvent.clear(champ);
  await userEvent.type(champ, texte);
  await userEvent.click(screen.getByRole("button", { name: "Ajouter ou corriger" }));
}

beforeEach(() => {
  proposerEquipe.mockResolvedValue(PROPOSITION);
  creerEquipe.mockResolvedValue({
    projet_id: PROJET.id,
    proposition_id: "equ-42",
    cree: true,
    agents: [],
    instances_total: 0,
  });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("① la demande atterrit sur l'équipe", () => {
  it("« ajoute quelqu'un pour la sécurité » ajoute une ligne retenue et signalée", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({
        ajouts: [SECURITE],
        reponse: "J'ajoute un rôle Sécurité applicative.",
      }),
    );
    await etapeChargee();

    await demander("ajoute quelqu'un pour la sécurité");

    const ligne = (await screen.findByText("Sécurité applicative")).closest("li");
    expect(ligne).not.toBeNull();
    const zone = within(ligne as HTMLElement);
    expect(zone.getByText("ajouté à votre demande")).toBeInTheDocument();
    expect(zone.getByRole("checkbox")).toBeChecked();
    // Un rôle hors gabarit ne se prétend descendant d'aucun.
    expect(zone.queryByText(/^gabarit/)).toBeNull();
    // La réponse du modèle se lit, annoncée.
    expect(screen.getByRole("status")).toHaveTextContent(
      "J'ajoute un rôle Sécurité applicative.",
    );
    // Le champ se vide : la demande a été prise en compte.
    expect(screen.getByRole("textbox", { name: /Il manque quelqu'un/ })).toHaveValue("");
    // Et le compte suit.
    expect(screen.getByRole("button", { name: "Créer l'équipe (3)" })).toBeEnabled();
  });

  it("le rôle ajouté repart à la création, playbook compris", async () => {
    corrigerEquipe.mockResolvedValue(correction({ ajouts: [SECURITE], reponse: "Ajouté." }));
    await etapeChargee();
    await demander("ajoute quelqu'un pour la sécurité");
    await screen.findByText("Sécurité applicative");

    await userEvent.click(screen.getByRole("button", { name: "Créer l'équipe (3)" }));

    await waitFor(() => expect(creerEquipe).toHaveBeenCalledTimes(1));
    const [, valides] = creerEquipe.mock.calls[0];
    const securite = (valides as { nom: string; playbook: string }[]).find(
      (r) => r.nom === "securite",
    );
    expect(securite?.playbook).toContain("Tu audites les dépendances.");
  });

  it("un retrait décoche, une remise recoche, une instance change le compteur", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({ retraits: ["tests"], instances: { dev: 2 }, reponse: "Fait." }),
    );
    await etapeChargee();

    await demander("pas de QA, et deux développeurs");

    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: /QA \/ Testeur/ })).not.toBeChecked(),
    );
    expect(screen.getAllByRole("spinbutton", { name: "Instances" })[0]).toHaveValue(2);
    expect(screen.getByRole("button", { name: "Créer l'équipe (2)" })).toBeEnabled();
  });
});

describe("② la demande porte l'équipe montrée", () => {
  it("cases décochées et instances comprises, avec les réponses du projet neuf", async () => {
    corrigerEquipe.mockResolvedValue(correction({ reponse: "Rien à changer." }));
    const choix = [{ cle: "nature", valeur: "service-api", deduit: false, parce_que: "" }];
    render(<EtapeEquipe projet={PROJET} choix={choix} onTermine={() => {}} />);
    await screen.findByText("Développeur backend");
    await userEvent.click(screen.getByRole("checkbox", { name: /QA \/ Testeur/ }));

    await demander("remets les tests");

    await waitFor(() => expect(corrigerEquipe).toHaveBeenCalledTimes(1));
    const [projet, texte, equipe, choixEnvoyes] = corrigerEquipe.mock.calls[0];
    expect(projet).toBe(PROJET.id);
    expect(texte).toBe("remets les tests");
    expect(equipe).toEqual([
      { nom: "dev", role: "Développeur backend", retenu: true, instances: 1 },
      { nom: "tests", role: "QA / Testeur", retenu: false, instances: 1 },
    ]);
    expect(choixEnvoyes).toEqual(choix);
  });
});

describe("③ une demande non comprise ne perd rien", () => {
  it("la liste reste, la phrase se lit, le texte reste dans le champ", async () => {
    corrigerEquipe.mockResolvedValue(
      correction({ reponse: "Je n'ai pas compris quel rôle vous voulez changer." }),
    );
    await etapeChargee();

    await demander("fais mieux");

    expect(await screen.findByRole("status")).toHaveTextContent(
      "Je n'ai pas compris quel rôle vous voulez changer.",
    );
    expect(screen.getByRole("textbox", { name: /Il manque quelqu'un/ })).toHaveValue(
      "fais mieux",
    );
    expect(screen.getByRole("button", { name: "Créer l'équipe (2)" })).toBeEnabled();
  });

  it("une panne du modèle se dit à l'endroit du geste, et la liste reste intacte", async () => {
    corrigerEquipe.mockRejectedValue(
      new Error("la demande n'a pas pu être comprise : quota épuisé"),
    );
    await etapeChargee();

    await demander("ajoute quelqu'un pour la sécurité");

    expect(await screen.findByRole("alert")).toHaveTextContent("Demande non traitée");
    expect(screen.queryByText("Sécurité applicative")).toBeNull();
    expect(screen.getByRole("textbox", { name: /Il manque quelqu'un/ })).toHaveValue(
      "ajoute quelqu'un pour la sécurité",
    );
  });

  it("une demande vide ne part pas", async () => {
    await etapeChargee();

    expect(screen.getByRole("button", { name: "Ajouter ou corriger" })).toBeDisabled();
  });
});

describe("④ aucun texte ne promet un geste absent", () => {
  it("le repli des écartés renvoie au champ qui fait le geste", async () => {
    await etapeChargee();

    await userEvent.click(screen.getByText(/rôles écartés, et pourquoi/));

    expect(screen.getByText(/dites-le ci-dessous/)).toBeInTheDocument();
    expect(screen.queryByText(/à la validation/)).toBeNull();
    expect(screen.queryByText(/créez-le depuis les écrans d'agents/)).toBeNull();
  });
});

describe("⑤ qui a composé l'équipe, et ce que compte l'analyse", () => {
  it("une équipe composée par les règles le dit, avec sa cause", async () => {
    proposerEquipe.mockResolvedValue({
      ...PROPOSITION,
      composition: {
        origine: "regles",
        raison:
          "composée par les règles des cinq gabarits : la composition pour le besoin " +
          "de votre projet n'a pas abouti (quota épuisé)",
      },
    });

    await etapeChargee();

    expect(screen.getByText(/composée par les règles des cinq gabarits/)).toBeInTheDocument();
  });

  it("une équipe composée par le modèle ne dit rien de plus", async () => {
    await etapeChargee();

    expect(screen.queryByText(/composée par les règles/)).toBeNull();
  });

  it("après une demande, la ligne de l'analyse dit qu'elle la précède", async () => {
    corrigerEquipe.mockResolvedValue(correction({ ajouts: [SECURITE], reponse: "Ajouté." }));
    await etapeChargee();
    expect(screen.queryByText(/avant vos demandes/)).toBeNull();

    await demander("ajoute quelqu'un pour la sécurité");

    expect(await screen.findByText(/avant vos demandes/)).toBeInTheDocument();
  });
});

describe("⑥ les raisons servies se lisent", () => {
  it("les `**…**` d'une raison d'autorisation sont rendus en gras", async () => {
    await etapeChargee();

    const gras = screen.getAllByText("sans vous demander");
    expect(gras[0].tagName).toBe("STRONG");
    expect(document.body.textContent).not.toContain("**");
  });

  it("un nombre impair de marqueurs laisse le texte intact", () => {
    expect(avecGras("un ** seul")).toEqual(["un ** seul"]);
  });
});

describe("appliquerCorrection — la règle, sans écran", () => {
  const montree = {
    roles: PROPOSITION.roles,
    retenus: new Set(["dev"]),
    instances: { dev: 1, tests: 1 },
  };

  it("un ajout déjà montré est ignoré, et rien de changé se dit", () => {
    const apres = appliquerCorrection(
      montree,
      correction({ ajouts: [role({ nom: "dev" })], reponse: "?" }),
    );

    expect(apres.roles).toHaveLength(2);
    expect(apres.ajoutes).toEqual([]);
    expect(apres.change).toBe(false);
  });

  it("un ajout vient en fin de liste, retenu, avec ses instances", () => {
    const apres = appliquerCorrection(
      montree,
      correction({ ajouts: [role({ nom: "securite", instances: 2 })] }),
    );

    expect(apres.roles.map((r) => r.nom)).toEqual(["dev", "tests", "securite"]);
    expect(apres.retenus.has("securite")).toBe(true);
    expect(apres.instances.securite).toBe(2);
    expect(apres.change).toBe(true);
  });

  it("une remise recoche", () => {
    const apres = appliquerCorrection(montree, correction({ remis: ["tests"] }));

    expect(apres.retenus.has("tests")).toBe(true);
  });
});

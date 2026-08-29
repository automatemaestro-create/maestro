/**
 * Le formulaire d'agent en **listes liées** (#255, lot 3 de #243).
 *
 * L'objectif du ticket tient en une phrase — « on ne peut plus composer une
 * configuration qui n'existe pas » — et se joue sur une **chaîne de
 * dépendances** : le fournisseur borne le modèle, le modèle décide de l'effort.
 * Rien de tout cela n'est visible d'un lint, d'un `next build` ni du typage :
 * les quatre champs restaient quatre chaînes de caractères indépendantes.
 *
 * Ce que ce fichier garde, critère par critère :
 *
 * ① **le rôle propose sans enfermer** — une liste *alimentée* par les rôles
 *    connus, la saisie libre intacte pour un rôle inédit. C'est un `<input>` +
 *    `<datalist>` et non un `<select>`, et le test le vérifie sur les deux
 *    moitiés : ce qui est proposé, *et* le fait qu'autre chose reste saisissable ;
 * ② **fournisseur puis modèle** — l'ordre à l'écran, l'offre restreinte au
 *    fournisseur choisi, et l'**invalidation visible** d'un modèle devenu
 *    impossible. Cette dernière est la seule qui parle d'un *changement* : elle
 *    ne s'observe qu'en jouant la transition, jamais sur un rendu figé ;
 * ③ **l'effort suit le modèle** — il apparaît quand le modèle en admet, avec sa
 *    valeur par défaut, et disparaît sinon. Le contrat du backend est repris
 *    tel quel : `efforts` vide veut dire « ce modèle ne se règle pas », et un
 *    modèle **hors gamme** n'annonce rien (`ModelProvider.efforts_admis`).
 *
 * Le réseau est débranché par `tests/setup.ts` (`chargerFournisseurs`,
 * `chargerCatalogue`) : aucun backend, conformément à docs/10 §8.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { CreationAgent } from "@/components/EditeurAgent";
import type { CatalogueFournisseurs } from "@/lib/types";

import {
  ficheCatalogueFactice,
  poserCatalogueAgents,
  poserFournisseurs,
} from "./aides";

/**
 * Deux fournisseurs aux gammes **volontairement dissemblables** — c'est ce qui
 * rend la liaison observable :
 *
 * - `claude` annonce deux modèles, dont un seul se règle en effort. Un modèle
 *   sans effort dans la même gamme est ce qui distingue « le sélecteur suit le
 *   modèle » de « le sélecteur suit le fournisseur » ;
 * - `local` a une gamme **fermée** (`modeles_libres: false`), le seul cas où le
 *   champ modèle n'a rien à laisser saisir. Aucun fournisseur du registre
 *   d'aujourd'hui n'est dans ce cas : le tester ici est ce qui empêche la
 *   branche de mourir sans qu'on s'en aperçoive le jour où l'un le devient.
 */
const DEUX_FOURNISSEURS: CatalogueFournisseurs = {
  fournisseurs: [
    {
      nom: "claude",
      modeles: [
        { nom: "claude-opus-5", libelle: "Opus 5", efforts: ["high", "xhigh"] },
        { nom: "claude-fable-5", libelle: "Fable 5", efforts: [] },
      ],
      modeles_libres: true,
      supporte: true,
      present_ici: true,
      utilisable_ici: true,
      modeles_ici: [],
      constats: [],
    },
    {
      nom: "local",
      modeles: [{ nom: "qwen2.5:3b", libelle: "Qwen 2.5", efforts: [] }],
      modeles_libres: false,
      supporte: true,
      present_ici: false,
      utilisable_ici: false,
      modeles_ici: [],
      constats: [],
    },
  ],
  hors_registre: [],
  incertitudes: [],
};

/** Monte le formulaire et attend que le catalogue des fournisseurs soit arrivé. */
async function formulaire() {
  const utilisateur = userEvent.setup();
  const vue = render(<CreationAgent onCreation={() => {}} />);
  await waitFor(() =>
    expect(
      within(screen.getByLabelText(/^Fournisseur/)).getAllByRole("option")
        .length,
    ).toBeGreaterThan(1),
  );
  return { utilisateur, ...vue };
}

/** Les valeurs proposées par un `<select>`, l'option « défaut » comprise. */
function valeursProposees(nom: RegExp): string[] {
  return within(screen.getByLabelText(nom))
    .getAllByRole("option")
    .map((o) => o.getAttribute("value") ?? "");
}

/**
 * Les valeurs suggérées par la `<datalist>` d'un champ libre — vide s'il n'en
 * porte aucune. Le `list` absent est un **état attendu** (rien à proposer), pas
 * un oubli : sans cette garde, `#` seul part en `SyntaxError` de sélecteur et
 * l'erreur parle de CSS là où le sujet est le catalogue.
 */
function valeursSuggerees(racine: HTMLElement, nom: RegExp): string[] {
  const champ = screen.getByLabelText(nom);
  const id = champ.getAttribute("list");
  if (!id) return [];
  const liste = racine.querySelector<HTMLDataListElement>(
    `#${CSS.escape(id)}`,
  );
  return [...(liste?.querySelectorAll("option") ?? [])].map(
    (o) => o.getAttribute("value") ?? "",
  );
}

describe("① le rôle se choisit dans une liste sans s'y enfermer", () => {
  it("propose les rôles des agents connus, dédoublonnés", async () => {
    poserCatalogueAgents([
      ficheCatalogueFactice({ nom: "dev", role: "Développeur" }),
      ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
      // Deux agents peuvent partager un rôle : la liste ne le répète pas.
      ficheCatalogueFactice({ nom: "dev-front", role: "Développeur" }),
    ]);
    const { container } = await formulaire();

    await waitFor(() =>
      expect(valeursSuggerees(container, /^Rôle/)).toEqual([
        "Développeur",
        "Testeur",
      ]),
    );
  });

  it("laisse saisir un rôle inédit", async () => {
    poserCatalogueAgents([ficheCatalogueFactice({ role: "Développeur" })]);
    const { utilisateur } = await formulaire();

    const champ = screen.getByLabelText(/^Rôle/);
    // La moitié qui compte : c'est un champ de saisie, pas un menu fermé.
    expect(champ).toHaveProperty("tagName", "INPUT");
    await utilisateur.type(champ, "Archéologue des données");
    expect(champ).toHaveValue("Archéologue des données");
  });

  it("sans catalogue lisible, reste le champ libre d'avant", async () => {
    // Défaut de `setup.ts` : aucun agent — donc aucune suggestion, et surtout
    // aucune liste vide accrochée au champ.
    const { container } = await formulaire();

    expect(screen.getByLabelText(/^Rôle/)).not.toHaveAttribute("list");
    expect(valeursSuggerees(container, /^Rôle/)).toEqual([]);
  });
});

describe("② fournisseur puis modèle", () => {
  it("présente le fournisseur avant le modèle", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    await formulaire();

    const position = screen
      .getByLabelText(/^Fournisseur/)
      .compareDocumentPosition(screen.getByLabelText(/^Modèle/));
    // Node.DOCUMENT_POSITION_FOLLOWING : le modèle vient après le fournisseur.
    expect(position & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("n'offre que les modèles du fournisseur choisi", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur, container } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );

    expect(valeursSuggerees(container, /^Modèle/)).toEqual([
      "claude-opus-5",
      "claude-fable-5",
    ]);
    // Et surtout : jamais ceux du voisin.
    expect(valeursSuggerees(container, /^Modèle/)).not.toContain("qwen2.5:3b");
  });

  it("ferme le champ modèle quand la gamme du fournisseur l'est", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "local",
    );

    // `modeles_libres: false` — annoncer une gamme, c'est s'y tenir.
    expect(screen.getByLabelText(/^Modèle/)).toHaveProperty(
      "tagName",
      "SELECT",
    );
    expect(valeursProposees(/^Modèle/)).toEqual(["", "qwen2.5:3b"]);
  });

  it("invalide visiblement un modèle devenu impossible", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(
      screen.getByLabelText(/^Modèle/),
      "claude-opus-5",
    );
    expect(screen.getByLabelText(/^Modèle/)).toHaveValue("claude-opus-5");

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "local",
    );

    // Vidé — et non laissé en place : c'est la moitié « invalide ».
    expect(screen.getByLabelText(/^Modèle/)).toHaveValue("");
    // Dit — et non silencieux : c'est la moitié « visiblement ».
    const annonce = screen.getByRole("status");
    expect(annonce).toHaveTextContent(/claude-opus-5/);
    expect(annonce).toHaveTextContent(/local/);
  });

  it("cesse d'annoncer le retrait dès qu'un modèle est de nouveau choisi", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-opus-5");
    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "local",
    );
    expect(screen.getByRole("status")).toBeInTheDocument();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Modèle/),
      "qwen2.5:3b",
    );

    // L'annonce parlait d'un champ vidé : elle n'a plus d'objet une fois qu'il
    // ne l'est plus, et la laisser dirait le contraire de l'écran.
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("ne touche pas à un modèle que le nouveau fournisseur offre encore", async () => {
    poserFournisseurs({
      ...DEUX_FOURNISSEURS,
      fournisseurs: DEUX_FOURNISSEURS.fournisseurs.map((f) =>
        // Les deux servent le même nom : changer de fournisseur n'a alors
        // aucune raison de vider quoi que ce soit.
        f.nom === "local"
          ? {
              ...f,
              modeles: [
                { nom: "claude-opus-5", libelle: "Opus 5", efforts: [] },
              ],
            }
          : f,
      ),
    });
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-opus-5");
    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "local",
    );

    expect(screen.getByLabelText(/^Modèle/)).toHaveValue("claude-opus-5");
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("③ l'effort suit le modèle", () => {
  it("apparaît quand le modèle en admet, sur sa valeur par défaut", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    // Tant qu'aucun modèle n'est choisi, le réglage n'a pas d'objet.
    expect(screen.queryByLabelText(/^Effort/)).toBeNull();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-opus-5");

    const effort = screen.getByLabelText(/^Effort/);
    // Sa valeur par défaut : aucun réglage — donc le régime du fournisseur,
    // ce que `effort: null` veut dire de bout en bout.
    expect(effort).toHaveValue("");
    expect(valeursProposees(/^Effort/)).toEqual(["", "high", "xhigh"]);
  });

  it("disparaît sur un modèle qui n'en admet aucun", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-fable-5");

    // `efforts: []` est une réponse à part entière : « ce modèle ne se règle
    // pas en effort », pas « on ne sait pas ».
    expect(screen.queryByLabelText(/^Effort/)).toBeNull();
  });

  it("disparaît sur un modèle hors gamme, faute de savoir ce qu'il admet", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(
      screen.getByLabelText(/^Modèle/),
      "claude-sonnet-9-inedit",
    );

    // Miroir exact de `ModelProvider.efforts_admis` : hors gamme, on ne sait
    // rien, et supposer serait le seul moyen d'envoyer un réglage refusé.
    expect(screen.queryByLabelText(/^Effort/)).toBeNull();
  });

  it("retire un effort que le nouveau modèle n'admet plus", async () => {
    poserFournisseurs(DEUX_FOURNISSEURS);
    const { utilisateur } = await formulaire();

    await utilisateur.selectOptions(
      screen.getByLabelText(/^Fournisseur/),
      "claude",
    );
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-opus-5");
    await utilisateur.selectOptions(screen.getByLabelText(/^Effort/), "xhigh");
    expect(screen.getByLabelText(/^Effort/)).toHaveValue("xhigh");

    // Le modèle change pour un qui ne se règle pas : le réglage ne peut pas
    // survivre en douce à ce qui le justifiait.
    await utilisateur.clear(screen.getByLabelText(/^Modèle/));
    await utilisateur.type(screen.getByLabelText(/^Modèle/), "claude-fable-5");

    expect(screen.queryByLabelText(/^Effort/)).toBeNull();
  });
});

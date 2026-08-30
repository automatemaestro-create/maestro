/**
 * La section « **Fournisseurs & modèles** » des Paramètres (#121), réalignée sur
 * le contrat de la vague « fiche agent v3 » (#243) par son lot final.
 *
 * Ce n'est pas un lot qui a livré sans tests : c'est un écran **qui n'a pas
 * bougé** pendant que le contrat qu'il affiche changeait trois fois. Il vaut donc
 * la peine d'écrire ce qu'il doit dire, parce que la panne est silencieuse — la
 * table s'affiche, elle est simplement fausse :
 *
 * ① **trois réglages, pas deux** (#253) — l'effort a rejoint le fournisseur et le
 *    modèle, et il atteint l'exécution. Une vue qui résume « ce que chaque agent
 *    consomme » et en tait un tiers est fausse en silence ;
 * ② **la provenance se lit** (#259) — `defaut_surcharge` rendu brut donnait
 *    « defaut_surcharge » à l'écran, et `libelleOrigine` disait « personnalisé »
 *    d'un agent qui ne l'est pas. Le troisième état est *du code, surchargé* : son
 *    rôle, ses compétences et son playbook viennent toujours du code ;
 * ③ **deux héritages qui ne se confondent pas** — un réglage absent partout suit
 *    l'exécution (`MAESTRO_PROVIDER`/`MAESTRO_MODEL`) ; un réglage d'agent du code
 *    non surchargé suit le **code**. Les dire d'un même mot ferait chercher dans
 *    le `.env` ce qui est écrit dans `maestro/agents/catalog.py` ;
 * ④ **le renvoi mène où l'on écrit** — la page Catalogue de #73 n'existe plus, la
 *    fiche à onglets l'a absorbée (#190).
 *
 * Le filtre par origine de la liste des agents est jugé ici aussi : c'est le même
 * écart, sur la même donnée — « Du code » doit retenir un agent surchargé.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ParametresFournisseurs } from "@/components/parametres/ParametresFournisseurs";
import {
  AGENT_SOURCE_DEFAUT,
  AGENT_SOURCE_PERSONNALISE,
  AGENT_SOURCE_SURCHARGE,
} from "@/lib/types";
import {
  ORIGINES_AGENT,
  composerLignesAgents,
  libelleOrigine,
  vueDesAgents,
} from "@/lib/vueAgents";

import { ficheCatalogueFactice, poserCatalogueAgents } from "./aides";

/** La ligne de la table qui parle de `nom`. */
function ligne(nom: string): HTMLElement {
  const cellule = screen.getByRole("cell", { name: nom });
  const rangee = cellule.closest("tr");
  if (rangee === null) throw new Error(`aucune ligne pour ${nom}`);
  return rangee;
}

/** Monte la section et attend que le catalogue soit arrivé. */
async function monter() {
  render(<ParametresFournisseurs />);
  await screen.findByRole("table");
}

describe("① les trois réglages de modèle sont servis", () => {
  it("porte une colonne par réglage, effort compris", async () => {
    poserCatalogueAgents([ficheCatalogueFactice({ nom: "dev" })]);

    await monter();

    // L'effort est le troisième réglage depuis #253, et le seul des trois qui,
    // avec le modèle, atteigne réellement l'exécution.
    expect(
      screen.getAllByRole("columnheader").map((entete) => entete.textContent),
    ).toEqual(["Agent", "Fournisseur", "Modèle", "Effort", "Provenance"]);
  });

  it("rend les trois valeurs d'un agent personnalisé qui les pose toutes", async () => {
    poserCatalogueAgents([
      ficheCatalogueFactice({
        nom: "relecteur",
        source: AGENT_SOURCE_PERSONNALISE,
        fournisseur: "claude",
        modele: "claude-opus-5",
        effort: "xhigh",
        herite: [],
        reglages_du_code: null,
      }),
    ]);

    await monter();

    const cellules = within(ligne("relecteur")).getAllByRole("cell");
    expect(cellules.map((cellule) => cellule.textContent)).toEqual([
      "relecteur",
      "claude",
      "claude-opus-5",
      "xhigh",
      "personnalisé",
    ]);
  });
});

describe("② la provenance se lit, et nomme le troisième état", () => {
  it("rend les trois provenances en clair, jamais leur code", async () => {
    poserCatalogueAgents([
      ficheCatalogueFactice({ nom: "dev", source: AGENT_SOURCE_DEFAUT }),
      ficheCatalogueFactice({
        nom: "qa",
        source: AGENT_SOURCE_SURCHARGE,
        modele: "claude-opus-5",
        herite: ["fournisseur", "effort"],
      }),
      ficheCatalogueFactice({
        nom: "relecteur",
        source: AGENT_SOURCE_PERSONNALISE,
        herite: [],
        reglages_du_code: null,
      }),
    ]);

    await monter();

    expect(within(ligne("dev")).getByText("du code")).toBeInTheDocument();
    expect(within(ligne("qa")).getByText("du code, surchargé")).toBeInTheDocument();
    expect(within(ligne("relecteur")).getByText("personnalisé")).toBeInTheDocument();
    // Le code de l'API n'a rien à faire à l'écran.
    expect(screen.queryByText(AGENT_SOURCE_SURCHARGE)).toBeNull();
  });

  it("ne fait pas d'un agent surchargé un agent personnalisé", () => {
    // Il n'a pas été dupliqué : son rôle, ses compétences et son playbook
    // continuent de venir du code. Le dire « personnalisé » annonçait l'inverse
    // du chantier #259.
    expect(libelleOrigine(AGENT_SOURCE_SURCHARGE)).toBe("du code, surchargé");
    expect(libelleOrigine(AGENT_SOURCE_DEFAUT)).toBe("du code");
    expect(libelleOrigine(AGENT_SOURCE_PERSONNALISE)).toBe("personnalisé");
  });
});

describe("③ les deux héritages ne se confondent pas", () => {
  it("marque « du code » un réglage que la définition livrée porte", async () => {
    poserCatalogueAgents([
      ficheCatalogueFactice({
        nom: "dev",
        source: AGENT_SOURCE_DEFAUT,
        // La fiche d'un agent du code sert la valeur **effective** : le modèle du
        // code, puisque rien ne le surcharge.
        modele: "claude-sonnet-5",
        herite: ["fournisseur", "modele", "effort"],
        reglages_du_code: {
          fournisseur: null,
          modele: "claude-sonnet-5",
          effort: null,
        },
      }),
    ]);

    await monter();

    const cellules = within(ligne("dev")).getAllByRole("cell");
    // Le modèle vient du code et le suivra : chercher cette valeur dans le `.env`
    // serait chercher au mauvais endroit.
    expect(cellules[2]).toHaveTextContent("claude-sonnet-5 du code");
    // Le fournisseur, lui, n'est déclaré nulle part : c'est l'exécution.
    expect(cellules[1]).toHaveTextContent("Hérité");
    expect(cellules[3]).toHaveTextContent("Hérité");
  });

  it("ne marque pas « du code » un réglage surchargé", async () => {
    poserCatalogueAgents([
      ficheCatalogueFactice({
        nom: "qa",
        source: AGENT_SOURCE_SURCHARGE,
        modele: "claude-opus-5",
        herite: ["fournisseur", "effort"],
        reglages_du_code: {
          fournisseur: null,
          modele: "claude-sonnet-5",
          effort: null,
        },
      }),
    ]);

    await monter();

    const modele = within(ligne("qa")).getAllByRole("cell")[2];
    expect(modele).toHaveTextContent("claude-opus-5");
    expect(modele).not.toHaveTextContent("du code");
  });

  it("ne redéduit pas la provenance en comparant la valeur au code", async () => {
    // Surchargé **à l'identique** : la valeur affichée est la même que celle du
    // code, mais elle ne le suivra plus. Comparer les deux ici rendrait les deux
    // cas indiscernables ; c'est `herite` qui tranche, et lui seul.
    poserCatalogueAgents([
      ficheCatalogueFactice({
        nom: "qa",
        source: AGENT_SOURCE_SURCHARGE,
        modele: "claude-sonnet-5",
        herite: ["fournisseur", "effort"],
        reglages_du_code: {
          fournisseur: null,
          modele: "claude-sonnet-5",
          effort: null,
        },
      }),
    ]);

    await monter();

    expect(within(ligne("qa")).getAllByRole("cell")[2]).not.toHaveTextContent(
      "du code",
    );
  });
});

describe("④ le renvoi mène là où l'on écrit", () => {
  it("envoie sur la fiche de l'agent, et dit ce qu'on y fait", async () => {
    poserCatalogueAgents([ficheCatalogueFactice({ nom: "dev" })]);

    await monter();

    const renvoi = screen.getByRole("link", { name: /fiche de l'agent/ });
    expect(renvoi).toHaveAttribute("href", "/agents");
    // La page Catalogue de #73 n'existe plus : la fiche à onglets l'a absorbée.
    expect(screen.queryByText(/page Catalogue/)).toBeNull();
    expect(screen.getByText(/sans dupliquer l'agent/)).toBeInTheDocument();
  });

  it("explique les deux héritages plutôt qu'un seul", async () => {
    poserCatalogueAgents([ficheCatalogueFactice({ nom: "dev" })]);

    await monter();

    expect(screen.getByText(/MAESTRO_PROVIDER/)).toBeInTheDocument();
    expect(screen.getByText(/« Du code » : le réglage vient de la définition/)).
      toBeInTheDocument();
  });

  it("renvoie aux agents même quand le catalogue est vide", async () => {
    poserCatalogueAgents([]);

    render(<ParametresFournisseurs />);

    expect(
      await screen.findByRole("link", { name: "Ouvrir la liste des agents" }),
    ).toBeInTheDocument();
  });
});

describe("le filtre par origine, sur le même écart", () => {
  const lignesPour = (sources: string[]) =>
    vueDesAgents(
      composerLignesAgents(
        sources.map((source, i) =>
          ficheCatalogueFactice({ nom: `agent-${i}`, source }),
        ),
        [],
      ),
      { recherche: "", role: "", origine: AGENT_SOURCE_DEFAUT, etat: "" },
      "nom",
    );

  it("retient un agent surchargé sous « Du code »", () => {
    const lignes = lignesPour([
      AGENT_SOURCE_DEFAUT,
      AGENT_SOURCE_SURCHARGE,
      AGENT_SOURCE_PERSONNALISE,
    ]);

    // Un agent dont on a surchargé le modèle vient toujours du code : l'égalité
    // stricte le faisait disparaître de la seule réponse où on le cherche.
    expect(lignes.map((ligne) => ligne.fiche.source)).toEqual([
      AGENT_SOURCE_DEFAUT,
      AGENT_SOURCE_SURCHARGE,
    ]);
  });

  it("garde deux entrées au filtre, quand l'API en distingue trois", () => {
    // Une troisième entrée obligerait à cocher deux cases pour une seule
    // question — « qui vient du code ? ».
    expect(ORIGINES_AGENT.map(({ valeur }) => valeur)).toEqual([
      AGENT_SOURCE_DEFAUT,
      AGENT_SOURCE_PERSONNALISE,
    ]);
  });
});

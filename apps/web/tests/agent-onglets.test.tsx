/**
 * L'**aiguillage** des facettes d'une fiche agent (`ContenuOngletAgent`, #190),
 * relu par le lot final de #243 parce que la vague lui a ajouté un cinquième
 * onglet (#266) et déplacé ce que deux autres montent (#259, #262).
 *
 * `tests/agents.test.tsx` garde la **barre** d'onglets — l'ordre des facettes,
 * l'onglet actif déduit du chemin, le retour à la liste. Ce qu'il ne vérifie
 * jamais, c'est que chaque onglet ouvre **le bon contenu** : la barre et
 * l'aiguillage sont deux fichiers, et rien ne les tient d'accord.
 *
 * Le typage n'y suffit pas, et c'est tout l'intérêt de ce fichier : le `switch`
 * de `ContenuOngletAgent` est exhaustif par construction (TypeScript refuserait
 * un cas manquant), mais il accepterait sans un mot qu'un cas branche le composant
 * du voisin — un copier-coller de deux lignes, invisible au lint comme au build,
 * et qui donne un onglet Playbook affichant le profil.
 *
 * Un test par facette, monté sur la clé **déclarée** (`ONGLETS_AGENT`) et non sur
 * une liste recopiée : un onglet ajouté sans être branché fait rougir le compte.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { ONGLETS_AGENT, type CleOngletAgent } from "@/lib/agents";
import { PLAYBOOK_SOURCE_DEFAUT } from "@/lib/types";

import { ficheCatalogueFactice, rendreAvecEtat } from "./aides";

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const aides = await import("./aides");
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(aides.projetsDeclares()),
    chargerJournal: () => Promise.resolve(aides.pageJournalCourante()),
    chargerFournisseurs: () => Promise.resolve(aides.fournisseursDuPoste()),
    chargerCatalogue: () => Promise.resolve(aides.catalogueAgents()),
    chargerAgentCatalogue: () =>
      Promise.resolve({ ...ficheCatalogueFactice({ nom: "dev" }), playbook: "" }),
    chargerPlaybook: () =>
      Promise.resolve({
        agent: "dev",
        role: "Développeur",
        version: 0,
        nb_versions: 0,
        source: PLAYBOOK_SOURCE_DEFAUT,
        cree_le: null,
        contenu: "## Mission",
      }),
    chargerVersionsPlaybook: () => Promise.resolve([]),
    chargerPropositionsPlaybook: () => Promise.resolve([]),
    chargerLexiquePlaybook: () => Promise.resolve({ structures: [], tournures: [] }),
  };
});

/**
 * Ce qu'une facette doit faire apparaître — un repère qui n'appartient qu'à elle.
 *
 * Chacun est choisi pour être **discriminant** : « Permissions de dev » ne peut
 * pas venir du profil, « Logs de dev » ne peut pas venir du chat. Un repère
 * partagé (le nom de l'agent, par exemple) rendrait le test vert quel que soit le
 * composant monté, ce qui est précisément le défaut qu'il cherche.
 */
const REPERES: Record<CleOngletAgent, { role: string; nom: string }> = {
  // La fiche factice est celle d'un agent **du code** : son profil est
  // `FicheDefaut` (réglages surchargeables, #259) et non l'éditeur complet d'un
  // agent personnalisé, qui rendrait « Configuration de dev ».
  profil: { role: "region", nom: "Fiche de dev" },
  playbook: { role: "region", nom: "Playbook de dev" },
  mcp: { role: "region", nom: "Permissions de dev" },
  chat: { role: "region", nom: "Chat avec dev" },
  logs: { role: "region", nom: "Logs de dev" },
};

describe("chaque facette ouvre son propre contenu", () => {
  it("couvre les facettes déclarées, et rien d'autre", () => {
    // La liste des repères se compare à la **déclaration** : un onglet ajouté à
    // `ONGLETS_AGENT` sans repère ici fait rougir avant d'être oublié.
    expect(Object.keys(REPERES).sort()).toEqual(
      ONGLETS_AGENT.map(({ cle }) => cle).sort(),
    );
  });

  for (const { cle, libelle } of ONGLETS_AGENT) {
    it(`ouvre « ${libelle} » sur son composant`, async () => {
      rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet={cle} />);

      const { role, nom } = REPERES[cle];
      expect(await screen.findByRole(role, { name: nom })).toBeInTheDocument();
    });
  }

  it("n'ouvre qu'une facette à la fois", async () => {
    rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="logs" />);
    await screen.findByRole("region", { name: "Logs de dev" });

    // Les cinq composants sont montés par le même aiguillage : en rendre deux
    // ferait une fiche qui empile ses facettes au lieu de les ouvrir.
    for (const { cle } of ONGLETS_AGENT.filter(({ cle }) => cle !== "logs")) {
      expect(screen.queryByRole("region", { name: REPERES[cle].nom })).toBeNull();
    }
  });
});

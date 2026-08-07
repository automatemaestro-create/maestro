/**
 * La page Journal (#249, lot 5 de #242) : le fil complet et ses filtres.
 *
 * Les tests de la vague sont différés au lot 8 (#252) et ce fichier n'y déroge
 * que pour ce que le lot **ajoute** : le tri du fil. Deux raisons de ne pas
 * l'attendre. Le filtrage est la seule logique de la page — le reste est du
 * rendu délégué à `FilActivite`, déjà couvert. Et écrire ces cas a débusqué ce
 * qu'aucun lint ni build ne voyait : le mock global de `@/lib/useControlTower`
 * (`setup.ts`) remplaçait le module **entier**, si bien qu'importer une de ses
 * constantes depuis une page la faisait échouer au montage, en test seulement.
 *
 * Ce qui reste au lot 8 : le langage visuel, le tableau de bord et le reste de
 * la vague. Ce qu'on ne trouvera pas ici non plus : une vérification que la
 * ligne d'activité s'affiche bien — c'est `tableau-de-bord.test.tsx` qui la
 * tient, pour les deux écrans à la fois, puisque c'est le même composant.
 */

import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import PageJournal from "@/app/journal/page";

import { evenementFactice, rendreAvecEtat } from "./aides";

const EVENEMENTS = [
  evenementFactice({
    type: "tache.statut",
    agent: "dev",
    tache_id: "T-1",
    titre: "Écrire les tests",
    statut: "terminee",
  }),
  evenementFactice({
    type: "agent.activite",
    agent: "qa",
    tache_id: "T-2",
    titre: "Relire la MR",
    statut: "en_cours",
  }),
  evenementFactice({
    type: "message.inter_agents",
    agent: "dev",
    tache_id: "",
    titre: "",
    detail: "ping pour la revue",
  }),
];

const lignes = () =>
  within(screen.getByRole("region", { name: "Activité en direct" })).queryAllByRole(
    "listitem",
  );

describe("la page Journal", () => {
  it("montre tout le fil, sans limite", () => {
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    expect(lignes()).toHaveLength(3);
    expect(screen.getByText(/3 événement\(s\)/)).toBeInTheDocument();
  });

  it("filtre par agent, puis se réinitialise", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    await utilisateur.selectOptions(
      screen.getByRole("combobox", { name: /Agent/ }),
      "qa",
    );
    expect(lignes()).toHaveLength(1);
    expect(screen.getByText(/1 événement\(s\) sur 3/)).toBeInTheDocument();
    await utilisateur.click(
      screen.getByRole("button", { name: /Réinitialiser/ }),
    );
    expect(lignes()).toHaveLength(3);
  });

  it("filtre par type d'événement, nommé en français", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    const liste = screen.getByRole("combobox", { name: /Type d'événement/ });
    expect(within(liste).getByRole("option", { name: "Statut de tâche" }));
    await utilisateur.selectOptions(liste, "tache.statut");
    expect(lignes()).toHaveLength(1);
  });

  it("filtre par tâche, titre en clair", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    const liste = screen.getByRole("combobox", { name: /Tâche/ });
    expect(within(liste).getByRole("option", { name: "Relire la MR" }));
    await utilisateur.selectOptions(liste, "T-2");
    expect(lignes()).toHaveLength(1);
  });

  it("cherche jusque dans le détail que la ligne n'affiche pas", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    await utilisateur.type(
      screen.getByRole("searchbox", { name: /Rechercher/ }),
      "ping",
    );
    expect(lignes()).toHaveLength(1);
  });

  it("ne garde que le notable, sur le filtre de la cloche", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    await utilisateur.click(
      screen.getByRole("checkbox", { name: /Notable seulement/ }),
    );
    // Seule la tâche terminée l'est (`estNotableNotification`).
    expect(lignes()).toHaveLength(1);
  });

  it("dit qu'aucun événement ne correspond plutôt qu'un fil vide", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<PageJournal />, { evenements: EVENEMENTS });
    await utilisateur.type(
      screen.getByRole("searchbox", { name: /Rechercher/ }),
      "zzz",
    );
    expect(screen.getByText(/Aucun événement ne correspond/)).toBeInTheDocument();
    expect(screen.queryByText(/Aucun événement reçu/)).toBeNull();
  });

  it("annonce la coupure du flux, dont il dépend entièrement", () => {
    rendreAvecEtat(<PageJournal />, { evenements: [], connecte: false });
    expect(screen.getByText(/Flux temps réel interrompu/)).toBeInTheDocument();
    // Le vide, lui, nomme le projet (#281) : les deux phrases répondent à deux
    // questions distinctes — « pourquoi ça ne se remplit plus » et « de quoi
    // est-ce vide » —, et une coupure ne doit pas les confondre.
    expect(screen.getByText(/Rien encore sur Dépensio/)).toBeInTheDocument();
  });
});

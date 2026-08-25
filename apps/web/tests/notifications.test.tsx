/**
 * Lot 3 de la refonte UX (#119) : le centre de notifications déroulant.
 *
 * Deux choses s'y jouent, et les deux méritent un filet :
 *
 * - **Le tri du notable.** Le flux temps réel est bavard (chaque transition de
 *   statut, chaque message inter-agents) ; la cloche ne doit remonter que ce
 *   qu'on veut savoir depuis une autre page. Ce tri vit dans `lib/evenements`,
 *   partagé avec le fil d'activité du tableau de bord — un résumé qui change
 *   ici change aux deux endroits.
 * - **La décision depuis le panneau.** Approuver ou refuser une validation
 *   (#48) sans quitter la page courante est le critère d'acceptation du lot :
 *   la décision doit bien partir vers le moteur, et un échec réseau doit rendre
 *   la main plutôt que de laisser les boutons éteints.
 */

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CentreNotifications } from "@/components/CentreNotifications";
import { IconePuce, IconeTache } from "@/components/Icones";
import {
  estNotableNotification,
  iconeEvenement,
  resumeEvenement,
} from "@/lib/evenements";
import {
  EVENEMENT_AGENT_ACTIVITE,
  EVENEMENT_AGENT_CAPACITE,
  EVENEMENT_MESSAGE_INTER_AGENTS,
  EVENEMENT_TACHE_REASSIGNATION,
  EVENEMENT_TACHE_STATUT,
  EVENEMENT_VALIDATION_DECISION,
  EVENEMENT_VALIDATION_DEMANDE,
} from "@/lib/types";

import { evenementFactice, rendreAvecEtat, validationFactice } from "./aides";

describe("le résumé d'un événement (lib/evenements)", () => {
  it("décrit une réassignation par sa destination", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_TACHE_REASSIGNATION,
          titre: "Écrire les tests",
          agent: "qa",
        }),
      ),
    ).toBe("« Écrire les tests » est confiée à qa");
  });

  it("dit qui a fait quoi plutôt qu'un identifiant suivi d'un statut", () => {
    // #250 : « tache-42 — Terminée (dev) » est le vocabulaire du bus, pas celui
    // de quelqu'un qui regarde travailler son équipe.
    expect(
      resumeEvenement(
        evenementFactice({ type: EVENEMENT_TACHE_STATUT, statut: "terminee" }),
      ),
    ).toBe("dev a terminé « Écrire les tests »");
  });

  it("dit l'activation d'un agent et son nombre d'instances", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_AGENT_CAPACITE,
          agent: "dev",
          statut: "active",
          instances: 3,
        }),
      ),
    ).toBe("dev est activé — jusqu'à 3 tâche(s) en parallèle");
  });

  it("dit la désactivation sans inventer d'instances", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_AGENT_CAPACITE,
          agent: "dev",
          statut: "desactive",
          instances: null,
        }),
      ),
    ).toBe("dev est désactivé et ne recevra plus de tâches");
  });

  it("distingue une validation approuvée d'une refusée", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_VALIDATION_DECISION,
          statut: "approuvee",
        }),
      ),
    ).toContain("Vous avez approuvé");
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_VALIDATION_DECISION,
          statut: "refusee",
        }),
      ),
    ).toContain("Vous avez refusé");
  });

  it("se rabat sur le tache_id quand l'événement n'a pas de titre", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_TACHE_REASSIGNATION,
          titre: "",
          tache_id: "T-42",
          agent: "qa",
        }),
      ),
    ).toBe("« T-42 » est confiée à qa");
  });

  it("reste lisible sur un type d'événement inconnu", () => {
    // Le flux peut s'enrichir côté backend sans que l'UI soit redéployée : une
    // ligne approximative vaut mieux qu'un « undefined » dans la liste.
    const resume = resumeEvenement(
      evenementFactice({ type: "trucmuche.inedit", agent: "dev" }),
    );
    expect(resume).toContain("dev");
    expect(resume).not.toContain("undefined");
  });

  it("coiffe chaque événement d'une icône, puce neutre par défaut", () => {
    // #245 : ce sont désormais des composants du jeu SVG, plus des émojis.
    expect(iconeEvenement(evenementFactice({ type: EVENEMENT_TACHE_STATUT }))).toBe(
      IconeTache,
    );
    expect(iconeEvenement(evenementFactice({ type: "trucmuche.inedit" }))).toBe(
      IconePuce,
    );
  });
});

describe("le tri du notable (estNotableNotification)", () => {
  it("retient les validations et les changements de capacité", () => {
    for (const type of [
      EVENEMENT_VALIDATION_DEMANDE,
      EVENEMENT_VALIDATION_DECISION,
      EVENEMENT_AGENT_CAPACITE,
    ]) {
      expect(estNotableNotification(evenementFactice({ type }))).toBe(true);
    }
  });

  it("retient une tâche terminée, en échec ou bloquée", () => {
    for (const statut of ["terminee", "echec", "bloquee"]) {
      expect(
        estNotableNotification(
          evenementFactice({ type: EVENEMENT_TACHE_STATUT, statut }),
        ),
      ).toBe(true);
    }
  });

  it("écarte les transitions intermédiaires d'une tâche", () => {
    // « assignée » et « en cours » sont le quotidien du tableau de bord : les
    // remonter dans la cloche la rendrait illisible.
    for (const statut of ["assignee", "en_cours"]) {
      expect(
        estNotableNotification(
          evenementFactice({ type: EVENEMENT_TACHE_STATUT, statut }),
        ),
      ).toBe(false);
    }
  });

  it("écarte le menu fretin temps réel", () => {
    for (const type of [EVENEMENT_AGENT_ACTIVITE, EVENEMENT_MESSAGE_INTER_AGENTS]) {
      expect(estNotableNotification(evenementFactice({ type }))).toBe(false);
    }
  });
});

describe("la cloche (CentreNotifications)", () => {
  const ouvrir = async (utilisateur: ReturnType<typeof userEvent.setup>) => {
    await utilisateur.click(screen.getByRole("button", { name: /Notifications/ }));
    // `dialog` et non `menu` depuis #536 : le panneau porte des sections et des
    // cartes d'arbitrage, jamais d'entrées `menuitem`.
    return screen.getByRole("dialog", { name: "Notifications" });
  };

  it("ne porte pas de badge quand rien n'attend d'arbitrage", () => {
    rendreAvecEtat(<CentreNotifications />);
    expect(
      screen.getByRole("button", { name: "Notifications" }),
    ).toBeInTheDocument();
  });

  it("compte les validations en attente jusque dans son étiquette", () => {
    rendreAvecEtat(<CentreNotifications />, {
      validations: [
        validationFactice({ tache_id: "T-1" }),
        validationFactice({ tache_id: "T-2" }),
      ],
    });
    expect(
      screen.getByRole("button", { name: "Notifications — 2 validations en attente" }),
    ).toBeInTheDocument();
  });

  it("accorde le singulier sur une seule demande", () => {
    rendreAvecEtat(<CentreNotifications />, {
      validations: [validationFactice()],
    });
    expect(
      screen.getByRole("button", { name: "Notifications — 1 validation en attente" }),
    ).toBeInTheDocument();
  });

  it("ne compte pas les demandes déjà tranchées", () => {
    rendreAvecEtat(<CentreNotifications />, {
      validations: [
        validationFactice({ tache_id: "T-1", statut: "approuvee" }),
        validationFactice({ tache_id: "T-2", statut: "refusee" }),
      ],
    });
    expect(
      screen.getByRole("button", { name: "Notifications" }),
    ).toBeInTheDocument();
  });

  it("plafonne l'affichage du badge à « 9+ »", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />, {
      validations: Array.from({ length: 12 }, (_, i) =>
        validationFactice({ tache_id: `T-${i}` }),
      ),
    });
    expect(screen.getByText("9+")).toBeInTheDocument();
    // Le compte exact reste dans le panneau et dans l'étiquette du bouton.
    const panneau = await ouvrir(utilisateur);
    expect(within(panneau).getByText("12 à valider")).toBeInTheDocument();
  });

  it("donne le contexte de chaque demande dans son panneau", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />, {
      validations: [
        validationFactice({
          titre: "Publier la version 1.2",
          agent: "devops",
          description: "Pousser l'image en production",
          raison: "Action irréversible",
        }),
      ],
    });
    const panneau = await ouvrir(utilisateur);
    expect(within(panneau).getByText("Publier la version 1.2")).toBeInTheDocument();
    expect(within(panneau).getByText(/devops/)).toBeInTheDocument();
    expect(
      within(panneau).getByText("Pousser l'image en production"),
    ).toBeInTheDocument();
    expect(within(panneau).getByText(/Action irréversible/)).toBeInTheDocument();
  });

  it("fait remonter une approbation au moteur", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(<CentreNotifications />, {
      validations: [validationFactice({ tache_id: "T-7" })],
      decider,
    });
    const panneau = await ouvrir(utilisateur);
    await utilisateur.click(within(panneau).getByRole("button", { name: "Approuver" }));
    expect(decider).toHaveBeenCalledWith("T-7", true);
  });

  it("fait remonter un refus au moteur", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(<CentreNotifications />, {
      validations: [validationFactice({ tache_id: "T-7" })],
      decider,
    });
    const panneau = await ouvrir(utilisateur);
    await utilisateur.click(within(panneau).getByRole("button", { name: "Refuser" }));
    expect(decider).toHaveBeenCalledWith("T-7", false);
  });

  it("rend la main quand la décision n'a pas pu partir", async () => {
    // Le backend est injoignable : la carte doit dire pourquoi et laisser
    // réessayer — c'est le seul cas où les boutons se rallument, la carte
    // disparaissant d'elle-même en cas de succès.
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockRejectedValue(new Error("API injoignable"));
    rendreAvecEtat(<CentreNotifications />, {
      validations: [validationFactice()],
      decider,
    });
    const panneau = await ouvrir(utilisateur);
    await utilisateur.click(within(panneau).getByRole("button", { name: "Approuver" }));

    await waitFor(() =>
      expect(within(panneau).getByText("API injoignable")).toBeInTheDocument(),
    );
    expect(within(panneau).getByRole("button", { name: "Approuver" })).toBeEnabled();
  });

  it("reste consultable quand plus rien n'attend d'arbitrage", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />, {
      evenements: [
        evenementFactice({ type: EVENEMENT_TACHE_STATUT, statut: "terminee" }),
      ],
    });
    const panneau = await ouvrir(utilisateur);
    expect(
      within(panneau).getByText("Aucune validation en attente."),
    ).toBeInTheDocument();
    expect(within(panneau).getByText(/a terminé/)).toBeInTheDocument();
  });

  it("ne liste dans l'activité que les événements notables", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />, {
      evenements: [
        evenementFactice({ type: EVENEMENT_TACHE_STATUT, statut: "echec" }),
        evenementFactice({ type: EVENEMENT_AGENT_ACTIVITE, agent: "bruit" }),
        evenementFactice({ type: EVENEMENT_MESSAGE_INTER_AGENTS, agent: "bruit" }),
      ],
    });
    const panneau = await ouvrir(utilisateur);
    const activite = within(panneau).getByRole("region", {
      name: "Activité récente",
    });
    expect(within(activite).getAllByRole("listitem")).toHaveLength(1);
  });

  it("borne l'activité récente à huit lignes", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />, {
      evenements: Array.from({ length: 20 }, (_, i) =>
        evenementFactice({
          type: EVENEMENT_TACHE_STATUT,
          statut: "terminee",
          tache_id: `T-${i}`,
          horodatage: `2026-07-28T10:${String(i).padStart(2, "0")}:00Z`,
        }),
      ),
    });
    const panneau = await ouvrir(utilisateur);
    const activite = within(panneau).getByRole("region", {
      name: "Activité récente",
    });
    expect(within(activite).getAllByRole("listitem")).toHaveLength(8);
  });

  it("le dit quand il n'y a rien à raconter", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />);
    const panneau = await ouvrir(utilisateur);
    expect(
      within(panneau).getByText("Rien de notable pour l'instant."),
    ).toBeInTheDocument();
  });

  it("se referme sur Échap", async () => {
    const utilisateur = userEvent.setup();
    rendreAvecEtat(<CentreNotifications />);
    await ouvrir(utilisateur);
    await utilisateur.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

/**
 * La vérification d'une tâche (#1177) : une tâche n'est « Terminée » qu'une fois
 * ses critères vérifiés en l'exécutant, et sa dernière vérification — verdict,
 * contrôle par contrôle, preuve comprise — se lit dans son détail et dans le fil.
 *
 * Trois lectures : ce que le flux sert, normalisé (`verificationDe`) ; la phrase
 * du fil pour une étape `:verification` (`resumeEvenement`) ; et le panneau de
 * détail qui la rend (`PanneauDetailTache`, via le Kanban).
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Kanban } from "@/components/Kanban";
import { detailDe, verificationDe } from "@/lib/detailTache";
import { resumeEvenement } from "@/lib/evenements";
import { libelleStatut } from "@/lib/format";
import {
  CONSTAT_NON_JOUE,
  CONSTAT_NON_TENU,
  CONSTAT_TENU,
  EVENEMENT_AGENT_ACTIVITE,
  VERIFICATION_IMPOSSIBLE,
  VERIFICATION_NON_TENUE,
  VERIFICATION_TENUE,
  type VerificationTache,
} from "@/lib/types";

import { agentFactice, evenementFactice, projetFactice, tacheFactice } from "./aides";

/** Une vérification telle que l'API la sert — un critère tenu, un qui ne l'est pas. */
function verificationFactice(
  partiel: Partial<VerificationTache> = {},
): VerificationTache {
  return {
    statut: VERIFICATION_NON_TENUE,
    resume: "1/2 critère(s) tenu(s)",
    empechement: "",
    livraison: 2,
    constats: [
      {
        critere: "le module s'importe",
        etat: CONSTAT_TENU,
        preuve: "",
        commande: "python -c 'import app'",
        code: 0,
      },
      {
        critere: "les tests passent",
        etat: CONSTAT_NON_TENU,
        preuve: "FAILED tests/test_app.py::test_route - assert 500 == 200",
        commande: "pytest -q",
        code: 1,
      },
    ],
    ...partiel,
  };
}

describe("verificationDe", () => {
  it("une tâche sans vérification n'en porte aucune", () => {
    expect(verificationDe(tacheFactice())).toBeNull();
    expect(verificationDe(tacheFactice({ verification: null }))).toBeNull();
  });

  it("rend le verdict, la livraison et chaque contrôle avec sa preuve", () => {
    const verification = verificationDe(
      tacheFactice({ verification: verificationFactice() }),
    );

    expect(verification).not.toBeNull();
    expect(verification?.statut).toBe(VERIFICATION_NON_TENUE);
    expect(verification?.livraison).toBe(2);
    expect(verification?.tenus).toBe(1);
    expect(verification?.constats[1]).toEqual({
      critere: "les tests passent",
      etat: CONSTAT_NON_TENU,
      preuve: "FAILED tests/test_app.py::test_route - assert 500 == 200",
      commande: "pytest -q",
      code: 1,
    });
  });

  it("un état inconnu n'est jamais lu comme tenu", () => {
    const verification = verificationDe(
      tacheFactice({
        verification: verificationFactice({
          statut: "un_statut_futur",
          constats: [
            { critere: "c", etat: "etrange", preuve: "", commande: "", code: null },
          ],
        }),
      }),
    );

    expect(verification?.constats[0].etat).toBe(CONSTAT_NON_JOUE);
    // Statut inconnu : déduit des constats — rien de tenu, rien de faux.
    expect(verification?.statut).toBe(VERIFICATION_IMPOSSIBLE);
  });

  it("retire un contrôle sans critère, qui n'aurait rien à dire", () => {
    const verification = verificationDe(
      tacheFactice({
        verification: verificationFactice({
          statut: "",
          constats: [
            { critere: " ", etat: CONSTAT_TENU, preuve: "", commande: "x", code: 0 },
            { critere: "c", etat: CONSTAT_TENU, preuve: "", commande: "y", code: 0 },
          ],
        }),
      }),
    );

    expect(verification?.constats.map((c) => c.commande)).toEqual(["y"]);
    expect(verification?.statut).toBe(VERIFICATION_TENUE);
  });

  it("une vérification impossible garde son empêchement", () => {
    const verification = verificationDe(
      tacheFactice({
        verification: {
          statut: VERIFICATION_IMPOSSIBLE,
          resume: "vérification impossible : aucun contrôle",
          empechement: "le vérificateur n'a établi aucun contrôle",
          constats: [],
        },
      }),
    );

    expect(verification?.empechement).toBe("le vérificateur n'a établi aucun contrôle");
    expect(verification?.statut).toBe(VERIFICATION_IMPOSSIBLE);
  });

  it("ouvre à elle seule le détail d'une tâche", () => {
    const detail = detailDe(tacheFactice({ verification: verificationFactice() }));

    expect(detail.vide).toBe(false);
    expect(detail.verification?.statut).toBe(VERIFICATION_NON_TENUE);
  });
});

/** Ouvre le panneau de détail de la seule tâche du Kanban, et le rend. */
async function panneauDe(verification: VerificationTache | null) {
  render(
    <Kanban
      taches={[tacheFactice({ titre: "Écrire l'API", statut: "echec", verification })]}
      agents={[agentFactice({ nom: "dev", role: "Développeur" })]}
      reassigner={vi.fn()}
      projet={projetFactice()}
    />,
  );
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Ouvrir le détail de la tâche/ }));
  return screen.getByRole("dialog");
}

describe("le détail d'une tâche montre sa vérification (variante A retenue)", () => {
  it("ouvre sur le verdict compté, avant la description", async () => {
    const panneau = await panneauDe(verificationFactice());
    const section = within(panneau).getByRole("region", { name: "Vérification" });

    expect(within(section).getByText("1/2")).toBeInTheDocument();
    expect(within(section).getByText("Non tenue")).toBeInTheDocument();
    expect(within(section).getByText("livraison n° 2")).toBeInTheDocument();
    // La section vient en tête du corps du panneau.
    const sections = within(panneau).getAllByRole("region");
    expect(sections[0]).toBe(section);
  });

  it("montre d'office la preuve d'un critère qui ne tient pas", async () => {
    const panneau = await panneauDe(verificationFactice());
    const section = within(panneau).getByRole("region", { name: "Vérification" });

    // La commande telle qu'elle a été jouée, et son code (réserve 1 du regard neuf)…
    expect(within(section).getByText("$ pytest -q → code 1")).toBeInTheDocument();
    // …puis la fin de sa sortie, sans geste à faire.
    const preuve = within(section).getByText(/assert 500 == 200/);
    expect(preuve.tagName).toBe("PRE");
    expect(preuve.closest("details")).toBeNull();
  });

  it("un critère tenu tient en une ligne qui se déplie", async () => {
    const panneau = await panneauDe(verificationFactice());
    const section = within(panneau).getByRole("region", { name: "Vérification" });

    const trace = within(section).getByText("$ python -c 'import app'");
    expect(trace.tagName).toBe("SUMMARY");
    expect(trace.className).toContain("truncate");
  });

  it("le code d'une commande tenue ne tombe jamais sous l'ellipse (réserve 1)", async () => {
    const panneau = await panneauDe(verificationFactice());
    const section = within(panneau).getByRole("region", { name: "Vérification" });

    // Relecture du 2026-09-27 : repliée, une commande longue coupait son
    // « → code 0 » avec elle. Le code se tient hors du sommaire tronqué.
    const trace = within(section).getByText("$ python -c 'import app'");
    const code = within(section).getByText("→ code 0");
    expect(trace.contains(code)).toBe(false);
    expect(code.className).toContain("shrink-0");
  });

  it("une lecture tenue dit ce qu'elle a trouvé dans le livrable (réserve 4)", async () => {
    const panneau = await panneauDe(
      verificationFactice({
        statut: VERIFICATION_TENUE,
        constats: [
          {
            critere: "le rapport rend un verdict",
            etat: CONSTAT_TENU,
            preuve: "« Verdict : conforme »",
            commande: "",
            code: null,
          },
        ],
      }),
    );

    expect(
      within(panneau).getByText("lu dans le livrable : « Verdict : conforme »"),
    ).toBeInTheDocument();
    expect(within(panneau).getByText("Vérifiée")).toBeInTheDocument();
  });

  it("chaque état se dit en toutes lettres, jamais par la seule couleur", async () => {
    const panneau = await panneauDe(
      verificationFactice({
        statut: VERIFICATION_IMPOSSIBLE,
        constats: [
          {
            critere: "le service démarre",
            etat: CONSTAT_NON_JOUE,
            preuve: "pas jouée — la commande sort du dossier du projet",
            commande: "sudo systemctl start app",
            code: null,
          },
        ],
      }),
    );
    const section = within(panneau).getByRole("region", { name: "Vérification" });

    expect(within(section).getByText("Non vérifiée")).toBeInTheDocument();
    expect(within(section).getByText(/— non joué/)).toBeInTheDocument();
    // Pas de code sur une commande qui n'a pas été jouée.
    expect(within(section).getByText("$ sudo systemctl start app")).toBeInTheDocument();
    expect(within(section).getByText(/sort du dossier du projet/)).toBeInTheDocument();
  });

  it("dit pourquoi rien n'a pu être vérifié", async () => {
    const panneau = await panneauDe({
      statut: VERIFICATION_IMPOSSIBLE,
      resume: "vérification impossible : aucun contrôle",
      empechement: "le vérificateur n'a établi aucun contrôle",
      constats: [],
    });

    expect(
      within(panneau).getByText("le vérificateur n'a établi aucun contrôle"),
    ).toBeInTheDocument();
  });

  it("dit qu'un livrable a été renvoyé par la QA", async () => {
    const panneau = await panneauDe(
      verificationFactice({
        livraison: undefined,
        renvoi: "revue",
        constats: [
          {
            critere: "la QA juge ce livrable conforme",
            etat: CONSTAT_NON_TENU,
            preuve: "« Revoir l'API » : 2 défaut(s) bloquant(s) — la route rend 500",
            commande: "",
            code: null,
          },
        ],
      }),
    );

    expect(within(panneau).getByText("renvoyée par la QA")).toBeInTheDocument();
    expect(within(panneau).getByText(/la route rend 500/)).toBeInTheDocument();
  });

  it("une tâche sans vérification rend le panneau d'avant", async () => {
    render(
      <Kanban
        taches={[tacheFactice({ description: "Écrire l'API." })]}
        agents={[agentFactice({ nom: "dev", role: "Développeur" })]}
        reassigner={vi.fn()}
        projet={projetFactice()}
      />,
    );
    await userEvent
      .setup()
      .click(screen.getByRole("button", { name: /Ouvrir le détail de la tâche/ }));

    expect(
      within(screen.getByRole("dialog")).queryByRole("region", { name: "Vérification" }),
    ).toBeNull();
  });
});

describe("le fil dit la vérification d'une livraison", () => {
  it.each([
    [VERIFICATION_TENUE, "Vérifiée"],
    [VERIFICATION_NON_TENUE, "Vérification non tenue"],
    [VERIFICATION_IMPOSSIBLE, "Vérification impossible"],
  ])("%s a un libellé en toutes lettres", (statut, libelle) => {
    expect(libelleStatut(statut)).toBe(libelle);
  });

  it("rend la phrase du moteur, pas le titre de la tâche", () => {
    expect(
      resumeEvenement(
        evenementFactice({
          type: EVENEMENT_AGENT_ACTIVITE,
          statut: VERIFICATION_NON_TENUE,
          detail: "1/2 critère(s) tenu(s)",
        }),
      ),
    ).toBe("Vérification non tenue — 1/2 critère(s) tenu(s)");
  });
});

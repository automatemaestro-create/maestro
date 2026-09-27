/**
 * La vérification d'une tâche (#1177) : une tâche n'est « Terminée » qu'une fois
 * ses critères vérifiés en l'exécutant, et sa dernière vérification — verdict,
 * contrôle par contrôle, preuve comprise — se lit dans son détail et dans le fil.
 *
 * Trois lectures : ce que le flux sert, normalisé (`verificationDe`) ; la phrase
 * du fil pour une étape `:verification` (`resumeEvenement`) ; et le panneau de
 * détail qui la rend (`PanneauDetailTache`, via le Kanban).
 */

import { describe, expect, it } from "vitest";

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

import { evenementFactice, tacheFactice } from "./aides";

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

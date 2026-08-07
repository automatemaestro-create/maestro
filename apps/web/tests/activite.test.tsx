/**
 * Les lignes d'activité (#250, lot 6 de « Control Tower v3 — socle visuel »).
 *
 * Le gros des tests de ce lot est **différé au lot 8** (docs/10 §5.1) ; ce
 * fichier ne garde que ce qui ne se relit pas à l'œil dans une revue de diff :
 *
 * - le **repli des rafales**, qui décide combien de lignes s'affichent et dans
 *   quel ordre le dépli les raconte ;
 * - les **seuils de l'horodatage relatif**, dont chaque borne est un choix (sous
 *   la minute on garde l'heure, au-delà de la semaine on la reprend) ;
 * - la **garde des types inconnus**, qui est précisément ce qu'on casse sans
 *   s'en apercevoir en réécrivant un `switch`.
 *
 * Les phrases elles-mêmes sont vérifiées là où elles l'étaient déjà, dans
 * `notifications.test.tsx` — c'est le même module.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FilActivite } from "@/components/FilActivite";
import {
  detailEvenement,
  grouperEvenements,
  resumeEvenement,
} from "@/lib/evenements";
import { formatDateHeure, formatHeure, formatHeureRelative } from "@/lib/format";
import { EVENEMENT_AGENT_ACTIVITE } from "@/lib/types";

import { evenementFactice } from "./aides";

const MINUTE = 60_000;
const HEURE = 60 * MINUTE;
const JOUR = 24 * HEURE;

describe("le repli des rafales (grouperEvenements)", () => {
  it("réduit N transitions d'une même tâche à une seule ligne", () => {
    const groupes = grouperEvenements([
      evenementFactice({ tache_id: "T-1", statut: "terminee" }),
      evenementFactice({ tache_id: "T-1", statut: "en_cours" }),
      evenementFactice({ tache_id: "T-1", statut: "assignee" }),
    ]);
    expect(groupes).toHaveLength(1);
    expect(groupes[0].evenements).toHaveLength(3);
    // La tête est le plus récent : le flux arrive du plus récent au plus ancien,
    // et c'est son état que la ligne annonce.
    expect(groupes[0].tete.statut).toBe("terminee");
  });

  it("ne mélange pas deux tâches", () => {
    const groupes = grouperEvenements([
      evenementFactice({ tache_id: "T-1" }),
      evenementFactice({ tache_id: "T-2" }),
    ]);
    expect(groupes).toHaveLength(2);
  });

  it("ne rapproche pas deux moments éloignés d'une même tâche", () => {
    // Le repli est consécutif : il resserre le bruit d'une tâche qui s'agite,
    // il ne réordonne jamais le fil.
    const groupes = grouperEvenements([
      evenementFactice({ tache_id: "T-1" }),
      evenementFactice({ tache_id: "T-2" }),
      evenementFactice({ tache_id: "T-1" }),
    ]);
    expect(groupes).toHaveLength(3);
  });

  it("replie les étapes hors tâche d'un même agent", () => {
    const groupes = grouperEvenements([
      evenementFactice({
        type: EVENEMENT_AGENT_ACTIVITE,
        tache_id: "",
        agent: "orchestrateur",
      }),
      evenementFactice({
        type: EVENEMENT_AGENT_ACTIVITE,
        tache_id: "",
        agent: "orchestrateur",
      }),
    ]);
    expect(groupes).toHaveLength(1);
  });

  it("ne groupe jamais ce qui ne porte ni tâche ni agent", () => {
    // Rien ne dit alors que deux lignes voisines parlent de la même chose.
    const groupes = grouperEvenements([
      evenementFactice({ tache_id: "", agent: "", run_id: "" }),
      evenementFactice({ tache_id: "", agent: "", run_id: "" }),
    ]);
    expect(groupes).toHaveLength(2);
  });

  it("rend un groupe d'un seul élément pour un événement seul", () => {
    // L'appelant n'a qu'un cas à rendre — pas « un événement ou un groupe ».
    const groupes = grouperEvenements([evenementFactice()]);
    expect(groupes).toHaveLength(1);
    expect(groupes[0].evenements).toHaveLength(1);
  });
});

describe("l'horodatage relatif (formatHeureRelative)", () => {
  const quand = "2026-07-28T10:00:00Z";
  const instant = new Date(quand).getTime();

  it("garde l'heure exacte sous la minute", () => {
    expect(formatHeureRelative(quand, instant + 30_000)).toBe(formatHeure(quand));
  });

  it("devient relatif au-delà de la minute", () => {
    expect(formatHeureRelative(quand, instant + 3 * MINUTE)).toBe("il y a 3 min");
    expect(formatHeureRelative(quand, instant + 2 * HEURE)).toBe("il y a 2 h");
    expect(formatHeureRelative(quand, instant + 3 * JOUR)).toBe("il y a 3 j");
  });

  it("reprend la date complète au-delà de la semaine", () => {
    // « il y a 23 j » ne situe plus rien.
    expect(formatHeureRelative(quand, instant + 30 * JOUR)).toBe(
      formatDateHeure(quand),
    );
  });

  it("rend l'heure absolue tant que l'horloge n'a pas démarré", () => {
    // Rendu serveur et première image : `Date.now()` diffère des deux côtés,
    // l'heure absolue est la seule qui hydrate sans diverger.
    expect(formatHeureRelative(quand, null)).toBe(formatHeure(quand));
  });

  it("n'écrit pas « il y a -2 min » sur des horloges désaccordées", () => {
    expect(formatHeureRelative(quand, instant - 2 * MINUTE)).toBe(
      formatHeure(quand),
    );
  });
});

describe("le détail brut (detailEvenement)", () => {
  it("rend ce que la phrase a laissé de côté", () => {
    const champs = detailEvenement(evenementFactice({ detail: "trace du moteur" }));
    const parLibelle = Object.fromEntries(
      champs.map((champ) => [champ.libelle, champ.valeur]),
    );
    expect(parLibelle["Tâche"]).toBe("T-1");
    expect(parLibelle["Statut"]).toBe("en_cours");
    expect(parLibelle["Agent"]).toBe("dev (Développeur)");
    expect(parLibelle["Détail"]).toBe("trace du moteur");
  });

  it("omet les champs qu'un événement ne renseigne pas", () => {
    const libelles = detailEvenement(
      evenementFactice({ tache_id: "", detail: "" }),
    ).map((champ) => champ.libelle);
    expect(libelles).not.toContain("Tâche");
    expect(libelles).not.toContain("Détail");
  });
});

describe("la ligne d'activité (LigneActivite)", () => {
  const rafale = [
    evenementFactice({
      tache_id: "T-1",
      statut: "terminee",
      horodatage: "2026-07-28T10:02:00Z",
    }),
    evenementFactice({
      tache_id: "T-1",
      statut: "en_cours",
      horodatage: "2026-07-28T10:01:00Z",
    }),
    evenementFactice({
      tache_id: "T-1",
      statut: "assignee",
      horodatage: "2026-07-28T10:00:00Z",
    }),
  ];

  it("annonce une rafale par son issue et le nombre d'étapes", () => {
    render(<FilActivite evenements={rafale} />);
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getAllByRole("listitem")).toHaveLength(1);
    const ligne = within(fil).getByRole("button", { name: /a terminé/ });
    expect(ligne).toHaveTextContent("3 étapes");
  });

  it("garde le détail brut à un clic, sur une ligne comme sur une rafale", () => {
    // C'est la contrepartie de « une phrase plutôt qu'un identifiant » :
    // l'identifiant a changé de plan, il n'a pas disparu.
    render(<FilActivite evenements={[evenementFactice()]} />);
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getByRole("button")).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(within(fil).getByText("T-1")).not.toBeVisible();
  });

  it("déplie la rafale et la raconte dans l'ordre où elle s'est jouée", async () => {
    const utilisateur = userEvent.setup();
    render(<FilActivite evenements={rafale} />);
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    await utilisateur.click(within(fil).getByRole("button", { name: /a terminé/ }));

    expect(within(fil).getByRole("button", { name: /a terminé/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    const etapes = within(fil)
      .getAllByRole("listitem")
      // La première entrée est la ligne elle-même ; les suivantes sont le dépli.
      .slice(1)
      .map((element) => element.textContent ?? "");
    expect(etapes).toHaveLength(3);
    expect(etapes[0]).toContain("prend en charge");
    expect(etapes[2]).toContain("a terminé");
  });

  it("compte les événements masqués, pas les lignes masquées", () => {
    // Trois transitions repliées en une ligne : l'aperçu en montre une, et il
    // en reste bien deux à aller voir ailleurs, pas zéro.
    render(<FilActivite evenements={[...rafale, evenementFactice({ tache_id: "T-9" })]} limite={1} />);
    expect(
      screen.getByText(/\+ 1 événement\(s\) plus anciens/),
    ).toBeInTheDocument();
  });
});

describe("la garde des types inconnus du front", () => {
  it("garde une ligne lisible au lieu de la faire disparaître", () => {
    // Le backend peut diffuser un type que ce front ne connaît pas encore.
    const resume = resumeEvenement(
      evenementFactice({ type: "trucmuche.inedit", agent: "dev" }),
    );
    expect(resume).toContain("dev");
    expect(resume).not.toContain("undefined");

    render(
      <FilActivite
        evenements={[evenementFactice({ type: "trucmuche.inedit" })]}
      />,
    );
    const fil = screen.getByRole("region", { name: "Activité en direct" });
    expect(within(fil).getAllByRole("listitem")).toHaveLength(1);
    expect(within(fil).getByRole("button")).toHaveTextContent("dev");
  });
});

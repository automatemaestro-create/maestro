/**
 * L'écran des **validations** — celui qui se décide vite (#273, lot 6/6 de #244).
 *
 * C'est l'écran le plus coûteux à mal rendre : une demande est **bloquante**, un
 * moteur est en pause et un run attend derrière. #272 l'a refondu ; ce qui
 * n'était couvert nulle part, c'est justement ce qui rend une décision juste —
 * l'ordre de la file, ce qu'on lit avant de trancher, et le sort d'un motif de
 * refus.
 *
 * Deux garanties de ce fichier valent d'être dites, parce qu'elles ne se voient
 * pas à la relecture du composant :
 *
 * - **le motif refermé est effacé**. Un motif conservé hors de l'écran partirait
 *   quand même avec le refus — un texte versé au journal du run que plus
 *   personne n'avait sous les yeux. « Sans motif » doit vouloir dire sans motif ;
 * - **une carte est keyée sur `tache_id`**. C'est tout ce qui tient le temps
 *   réel : une demande tranchée ailleurs démonte *sa* carte et emporte son état
 *   local. Sans cette clé, la file se décalant d'un cran, un motif écrit pour une
 *   demande se retrouverait attaché à la suivante — un refus motivé à côté de la
 *   plaque, que rien ne signalerait. Le test le prouve en **retirant la tête de
 *   file** pendant qu'un motif est en cours de frappe.
 *
 * Piège du harnais, le même qu'`etat-des-runs.test.tsx` : `useHorloge` lit un
 * vrai `Date.now()` en jsdom. Les âges se posent donc **relativement à
 * maintenant** (`ilYA`), jamais en date écrite en dur.
 *
 * Couvre :
 *
 * ① `fileDAttente` — la plus ancienne d'abord, les tranchées dehors, et une
 *    demande sans horodatage en queue plutôt qu'en tête ;
 * ② `formatAttente` — « depuis » et non « il y a », ses paliers, et les deux cas
 *    où l'on ne compte pas (horloge non démarrée, horloges désaccordées) ;
 * ③ la carte : ce qu'on lit pour trancher, dans l'ordre de la décision ;
 * ④ les gestes : approuver, refuser sec, refuser motivé, et l'échec qui rend la
 *    main ;
 * ⑤ les deux surfaces : même carte, densité différente — l'aperçu renvoie, le
 *    plein format déroule.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import {
  FileValidations,
  PanneauValidations,
  fileDAttente,
} from "@/components/PanneauValidations";
import { formatAttente } from "@/lib/format";
import {
  NATURE_AJOUT,
  NATURE_MODIFICATION,
  VALIDATION_APPROUVEE,
  VALIDATION_EN_ATTENTE,
  type Validation,
} from "@/lib/types";

import { rendreAvecEtat, validationFactice } from "./aides";

const MINUTE = 60_000;
const HEURE = 60 * MINUTE;
const JOUR = 24 * HEURE;

/** Un horodatage d'il y a `ms` — voir l'en-tête : l'horloge est celle du poste. */
function ilYA(ms: number): string {
  return new Date(Date.now() - ms).toISOString();
}

/** Une demande en attente depuis `ms`. */
function enAttenteDepuis(ms: number, partiel: Partial<Validation> = {}): Validation {
  return validationFactice({ horodatage: ilYA(ms), ...partiel });
}

// ── ① l'ordre de la file ─────────────────────────────────────────────────────

describe("fileDAttente (#272)", () => {
  it("met la plus ancienne en tête — celle qui retient un moteur depuis le plus longtemps", () => {
    const recente = enAttenteDepuis(2 * MINUTE, { tache_id: "T-recente" });
    const ancienne = enAttenteDepuis(3 * HEURE, { tache_id: "T-ancienne" });

    expect(fileDAttente([recente, ancienne]).map((v) => v.tache_id)).toEqual([
      "T-ancienne",
      "T-recente",
    ]);
  });

  it("laisse dehors ce qui a déjà été tranché", () => {
    const tranchee = validationFactice({
      tache_id: "T-tranchee",
      statut: VALIDATION_APPROUVEE,
    });

    expect(fileDAttente([tranchee, enAttenteDepuis(MINUTE)])).toHaveLength(1);
  });

  it("range une demande sans horodatage en dernier, jamais en tête", () => {
    // Elle n'a pas d'âge à faire valoir : la mettre devant ferait traiter
    // d'abord celle dont on sait le moins.
    const sansAge = validationFactice({ tache_id: "T-sans-age", horodatage: "" });

    expect(
      fileDAttente([sansAge, enAttenteDepuis(MINUTE, { tache_id: "T-datee" })]).map(
        (v) => v.tache_id,
      ),
    ).toEqual(["T-datee", "T-sans-age"]);
  });

  it("ne rend rien quand rien n'attend", () => {
    expect(fileDAttente([])).toEqual([]);
  });
});

// ── ② le temps d'attente ─────────────────────────────────────────────────────

describe("formatAttente (#272)", () => {
  const maintenant = Date.UTC(2026, 7, 27, 12, 0, 0);
  const ilYAExactement = (ms: number) => new Date(maintenant - ms).toISOString();

  it("dit « depuis » et non « il y a » — une attente dure, un fait est passé", () => {
    expect(formatAttente(ilYAExactement(3 * MINUTE), maintenant)).toBe("depuis 3 min");
    expect(formatAttente(ilYAExactement(2 * HEURE), maintenant)).toBe("depuis 2 h");
    expect(formatAttente(ilYAExactement(3 * JOUR), maintenant)).toBe("depuis 3 j");
  });

  it("ne fait pas faire la soustraction sous la minute", () => {
    expect(formatAttente(ilYAExactement(5_000), maintenant)).toBe(
      "depuis moins d'une minute",
    );
  });

  it("ne rend jamais une attente négative, horloges désaccordées comprises", () => {
    expect(formatAttente(ilYAExactement(-2 * MINUTE), maintenant)).toBe(
      "depuis moins d'une minute",
    );
  });

  it("repasse à la date au-delà de la semaine — ce n'est plus une attente", () => {
    expect(formatAttente(ilYAExactement(10 * JOUR), maintenant)).toMatch(/^depuis le /);
  });

  it("rend l'heure absolue tant que l'horloge n'a pas démarré", () => {
    // Identique sur le serveur et dans le navigateur : c'est ce qui évite de
    // faire diverger l'HTML hydraté.
    expect(formatAttente(ilYAExactement(3 * MINUTE), null)).toMatch(/^depuis \d/);
  });

  it("ne rend rien sans horodatage, et le brut s'il est illisible", () => {
    expect(formatAttente("", maintenant)).toBe("");
    expect(formatAttente("pas-une-date", maintenant)).toBe("pas-une-date");
  });
});

// ── ③ ce qu'on lit pour trancher ─────────────────────────────────────────────

describe("la carte d'une demande (#272/#581)", () => {
  it("met l'acte en tête quand il y en a un, le titre en dessous", async () => {
    // Depuis #573 c'est l'acte qui déclenche l'arbitrage : afficher « Rédiger le
    // README » au-dessus d'un `rm -rf` ferait trancher à côté.
    rendreAvecEtat(
      <PanneauValidations
        validations={[
          enAttenteDepuis(MINUTE, {
            titre: "Rédiger le README",
            outil: "Bash",
            arguments: { command: "rm -rf /tmp/x" },
          }),
        ]}
        decider={vi.fn()}
      />,
    );

    expect(screen.getByText("Appel de")).toBeInTheDocument();
    expect(screen.getByText("Bash")).toBeInTheDocument();
    expect(screen.getByText("command")).toBeInTheDocument();
    expect(screen.getByText("rm -rf /tmp/x")).toBeInTheDocument();
    // Le titre reste lisible, une place plus bas : il dit d'où vient l'acte.
    expect(screen.getByText(/Rédiger le README/)).toBeInTheDocument();
  });

  it("retombe sur le titre de la tâche quand la demande ne porte pas d'acte", () => {
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { titre: "Publier la version 1.2" })]}
        decider={vi.fn()}
      />,
    );

    expect(screen.getByText("Publier la version 1.2")).toBeInTheDocument();
    expect(screen.queryByText("Appel de")).not.toBeInTheDocument();
  });

  it("dit un outil sans paramètre au lieu de laisser un vide", () => {
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { outil: "ListeProjets", arguments: null })]}
        decider={vi.fn()}
      />,
    );

    expect(screen.getByText("Aucun argument")).toBeInTheDocument();
  });

  it("porte l'ancienneté au premier plan, et la raison de la classification", () => {
    rendreAvecEtat(
      <PanneauValidations
        validations={[
          enAttenteDepuis(3 * MINUTE, { raison: "Action irréversible", agent: "devops" }),
        ]}
        decider={vi.fn()}
      />,
    );

    expect(screen.getByText("depuis 3 min")).toBeInTheDocument();
    expect(screen.getByText(/Motif : Action irréversible/)).toBeInTheDocument();
    expect(screen.getByText(/Agent devops/)).toBeInTheDocument();
  });

  it("rend le diff d'une application dans le projet (#227)", () => {
    rendreAvecEtat(
      <PanneauValidations
        validations={[
          enAttenteDepuis(MINUTE, {
            diff: {
              fichiers: 2,
              ajouts: 12,
              suppressions: 3,
              branche: "maestro/run-1",
              base: "main",
              modifications: [
                {
                  chemin: "src/a.ts",
                  nature: NATURE_MODIFICATION,
                  ajouts: 10,
                  suppressions: 3,
                  binaire: false,
                },
                {
                  chemin: "src/b.png",
                  nature: NATURE_AJOUT,
                  ajouts: 0,
                  suppressions: 0,
                  binaire: true,
                },
              ],
            },
          }),
        ]}
        decider={vi.fn()}
      />,
    );

    expect(screen.getByText("2 fichiers")).toBeInTheDocument();
    expect(screen.getByText("Fusion de maestro/run-1 vers main")).toBeInTheDocument();
    expect(screen.getByText("binaire")).toBeInTheDocument();
  });
});

// ── ④ trancher ───────────────────────────────────────────────────────────────

describe("trancher une demande (#272)", () => {
  it("approuve en un geste, sans motif", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { tache_id: "T-9" })]}
        decider={decider}
      />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Approuver" }));

    // L'appel d'avant #272, à l'argument près : c'est ce qui garde
    // « approuver » hors de portée d'une régression du canal motivé.
    expect(decider).toHaveBeenCalledWith("T-9", true);
  });

  it("refuse en un geste quand on n'a rien à expliquer", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { tache_id: "T-9" })]}
        decider={decider}
      />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Refuser" }));

    expect(decider).toHaveBeenCalledWith("T-9", false);
  });

  it("fait partir le motif avec le refus, sans en faire une étape", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { tache_id: "T-9" })]}
        decider={decider}
      />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Motiver le refus" }));
    await utilisateur.type(
      screen.getByLabelText(/Motif du refus/),
      "La branche cible est la mauvaise",
    );
    // C'est toujours « Refuser » qui tranche — le motif s'ouvre à côté.
    await utilisateur.click(screen.getByRole("button", { name: "Refuser" }));

    expect(decider).toHaveBeenCalledWith("T-9", false, "La branche cible est la mauvaise");
  });

  it("efface le motif quand on le referme — « sans motif » veut dire sans motif", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(
      <PanneauValidations
        validations={[enAttenteDepuis(MINUTE, { tache_id: "T-9" })]}
        decider={decider}
      />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Motiver le refus" }));
    await utilisateur.type(screen.getByLabelText(/Motif du refus/), "écrit puis retiré");
    await utilisateur.click(screen.getByRole("button", { name: "Sans motif" }));
    await utilisateur.click(screen.getByRole("button", { name: "Refuser" }));

    // Un motif conservé hors de l'écran partirait quand même : c'est le texte
    // qu'on ne veut pas voir arriver au journal du run.
    expect(decider).toHaveBeenCalledWith("T-9", false);
  });

  it("n'annonce rien tant que rien n'est tranché", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    rendreAvecEtat(
      <PanneauValidations validations={[enAttenteDepuis(MINUTE)]} decider={decider} />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Motiver le refus" }));

    // Un formulaire ouvert n'est pas une décision prise (note technique du ticket).
    expect(decider).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Approuver" })).toBeEnabled();
  });

  it("rend la main et nomme la cause quand la décision n'est pas passée", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockRejectedValue(new Error("409 : déjà tranchée"));
    rendreAvecEtat(
      <PanneauValidations validations={[enAttenteDepuis(MINUTE)]} decider={decider} />,
    );

    await utilisateur.click(screen.getByRole("button", { name: "Approuver" }));

    expect(await screen.findByText("409 : déjà tranchée")).toBeInTheDocument();
    // Rendue seulement en cas d'échec : sur un succès, la carte se démonte au
    // rechargement et rouvrir les boutons inviterait à un second clic en 409.
    expect(screen.getByRole("button", { name: "Approuver" })).toBeEnabled();
  });

  it("n'attache pas à la suivante le motif écrit pour une demande tranchée ailleurs", async () => {
    const utilisateur = userEvent.setup();
    const decider = vi.fn().mockResolvedValue(undefined);
    const tete = enAttenteDepuis(2 * HEURE, { tache_id: "T-tete" });
    const suivante = enAttenteDepuis(MINUTE, { tache_id: "T-suivante" });
    // `render` nu et non `rendreAvecEtat` : le composant ne lit pas le contexte
    // du shell, et le `rerender` doit réconcilier **le même** arbre — c'est tout
    // ce que le test observe.
    const { rerender } = render(
      <FileValidations validations={[tete, suivante]} decider={decider} />,
    );

    // Un motif en cours de frappe sur la carte de tête — la première du DOM.
    await utilisateur.click(
      screen.getAllByRole("button", { name: "Motiver le refus" })[0],
    );
    await utilisateur.type(screen.getByLabelText(/Motif du refus/), "pour la tête");

    // …et la tête est tranchée ailleurs : sa carte se démonte, son état part avec.
    rerender(<FileValidations validations={[suivante]} decider={decider} />);

    expect(screen.queryByLabelText(/Motif du refus/)).not.toBeInTheDocument();
    await utilisateur.click(screen.getByRole("button", { name: "Refuser" }));
    expect(decider).toHaveBeenCalledWith("T-suivante", false);
  });
});

// ── ⑤ deux surfaces, une carte ───────────────────────────────────────────────

describe("l'aperçu et le plein format (#272)", () => {
  const file = [
    enAttenteDepuis(3 * HEURE, { tache_id: "T-1", titre: "La plus ancienne" }),
    enAttenteDepuis(2 * HEURE, { tache_id: "T-2", titre: "La deuxième" }),
    enAttenteDepuis(MINUTE, { tache_id: "T-3", titre: "La troisième" }),
  ];

  it("l'aperçu ne montre que la plus urgente, décidable sur place, et renvoie", () => {
    rendreAvecEtat(<PanneauValidations validations={file} decider={vi.fn()} />);

    expect(screen.getByText("La plus ancienne")).toBeInTheDocument();
    expect(screen.queryByText("La deuxième")).not.toBeInTheDocument();
    expect(screen.getByText("2 autres demandes attendent leur tour.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approuver" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /Ouvrir les validations/ }),
    ).toHaveAttribute("href", "/validations");
  });

  it("accorde la ligne de reste au singulier", () => {
    rendreAvecEtat(
      <PanneauValidations validations={file.slice(0, 2)} decider={vi.fn()} />,
    );

    expect(screen.getByText("1 autre demande attend son tour.")).toBeInTheDocument();
  });

  it("le plein format déroule tout, sans résumer les suivantes", () => {
    rendreAvecEtat(<FileValidations validations={file} decider={vi.fn()} />);

    // Ce qu'on lit pour trancher ne dépend pas de la place dans la file : les
    // suivantes portent les mêmes gestes que la tête.
    expect(screen.getAllByRole("button", { name: "Approuver" })).toHaveLength(3);
    expect(screen.getByText("La deuxième")).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Les suivantes", level: 3 }),
    ).toBeInTheDocument();
    // Et la page ne renvoie pas vers elle-même.
    expect(
      screen.queryByRole("link", { name: /Ouvrir les validations/ }),
    ).not.toBeInTheDocument();
  });

  it("compte ce qui attend, sur les deux surfaces", () => {
    const { unmount } = rendreAvecEtat(
      <PanneauValidations validations={file} decider={vi.fn()} />,
    );
    const apercu = screen.getByRole("region", { name: "Validations en attente" });
    expect(within(apercu).getByText("3")).toBeInTheDocument();
    unmount();

    rendreAvecEtat(<FileValidations validations={file} decider={vi.fn()} />);
    const plein = screen.getByRole("region", { name: "Validations en attente" });
    expect(within(plein).getByText("3")).toBeInTheDocument();
  });

  it("disparaît des deux côtés quand la file se vide", () => {
    const tranchee = validationFactice({ statut: VALIDATION_APPROUVEE });
    const { container, unmount } = rendreAvecEtat(
      <PanneauValidations validations={[tranchee]} decider={vi.fn()} />,
    );
    expect(container).toBeEmptyDOMElement();
    unmount();

    const { container: plein } = rendreAvecEtat(
      <FileValidations validations={[tranchee]} decider={vi.fn()} />,
    );
    expect(plein).toBeEmptyDOMElement();
  });

  it("le statut « en attente » est le seul qui met en file", () => {
    expect(
      fileDAttente([validationFactice({ statut: VALIDATION_EN_ATTENTE })]),
    ).toHaveLength(1);
  });
});

/**
 * **La durée d'une tâche est son temps de travail** (#989) — et ses attentes se
 * lisent à part.
 *
 * Le défaut, mesuré deux fois : 21 min 17 s annoncées pour 8 min 05 s de travail
 * (revue du 2026-08-26), 24 min 17 s pour ~11 min (retex du 2026-09-11, G4). Le
 * moteur rangeait déjà l'attente d'un créneau d'agent (#86) et celle de
 * l'atelier d'un projet (#839) hors du travail — « attendre son tour n'est pas
 * travailler », dit son propre commentaire — sans les compter ; l'écran, lui,
 * affichait l'horloge.
 *
 * **La forme retenue** (variante A, choisie par le regard neuf sur trois
 * variantes rendues, contre GitHub Actions, GitLab CI et Buildkite capturés en
 * direct) : la place compacte — carte de Kanban, nœud de pipeline, ligne de
 * grand livre — porte **un** chiffre, le travail ; l'attente est un fait
 * **nommé** dans le panneau de détail, et seulement quand elle est non nulle.
 *
 * Ce que ces tests gardent, et qui est exactement ce qui peut se défaire :
 *
 * 1. la place compacte affiche le **travail** et non l'horloge ;
 * 2. l'attente est nommée dans le détail, avec le travail **en regard** ;
 * 3. une tâche qui n'a rien attendu rend la carte d'avant, au pixel près — pas
 *    de section vide, pas de « — » de remplissage, pas de panneau qui s'ouvre
 *    sur rien ;
 * 4. le repli : un backend d'avant #989 ne sert pas la décomposition, et l'écran
 *    retombe alors sur l'horloge plutôt que sur un tiret.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Kanban } from "@/components/Kanban";
import { PanneauCouts } from "@/components/PanneauCouts";
import { attentesDe, detailDe } from "@/lib/detailTache";

import {
  agentFactice,
  coutExecutionFactice,
  coutTacheFactice,
  projetFactice,
  tacheFactice,
  usageFactice,
} from "./aides";

/** L'usage de la tâche du retex : 13 min 39 s d'horloge, 1 min 01 s de travail. */
function usageQuiAAttendu(partiel = {}) {
  return usageFactice({
    duree_ms: 819_000,
    duree_attente_creneau_ms: 0,
    duree_attente_atelier_ms: 758_000,
    duree_arbitrage_ms: 0,
    duree_attente_ms: 758_000,
    duree_execution_ms: 61_000,
    ...partiel,
  });
}

function rendreKanban(taches: ReturnType<typeof tacheFactice>[]) {
  render(
    <Kanban
      taches={taches}
      agents={[agentFactice({ nom: "qa", role: "Testeur" })]}
      reassigner={vi.fn()}
      projet={projetFactice()}
    />,
  );
}

async function ouvrirLeDetail() {
  const utilisateur = userEvent.setup();
  await utilisateur.click(
    screen.getByRole("button", { name: /Ouvrir le détail de la tâche/ }),
  );
  return utilisateur;
}

// --- ① La place compacte porte le travail, jamais l'horloge -----------------

describe("la carte du Kanban", () => {
  it("affiche le travail de la tâche, pas son horloge", () => {
    rendreKanban([tacheFactice({ usage: usageQuiAAttendu() })]);

    const carte = screen.getByRole("article");
    expect(within(carte).getByText("1 min 01 s")).toBeTruthy();
    // 13 min 39 s : le chiffre que la carte annonçait avant ce ticket.
    expect(within(carte).queryByText("13 min 39 s")).toBeNull();
  });

  it("ne porte pas l'attente elle-même — un seul chiffre dans la place compacte", () => {
    // La variante écartée (B) mettait les deux temps sur la ligne chrono : le
    // chiffre du travail y passait de deux lignes à quatre, et les deux durées,
    // de même graisse et de même format, se lisaient l'une pour l'autre. Aucune
    // des trois références ne charge ainsi son élément de liste.
    rendreKanban([tacheFactice({ usage: usageQuiAAttendu() })]);

    const carte = screen.getByRole("article");
    expect(within(carte).queryByText(/12 min 38 s/)).toBeNull();
  });

  it("nomme la mesure pour la lecture d'écran", () => {
    // Depuis #894 cette place rend déjà deux mesures de sens différent ; elle en
    // rend une troisième. « 1 min 01 s » ne dit pas de quoi c'est la durée.
    rendreKanban([tacheFactice({ usage: usageQuiAAttendu() })]);

    expect(screen.getByText("Travail")).toBeTruthy();
  });

  it("retombe sur l'horloge quand le backend ne sert pas la décomposition", () => {
    // Un backend d'avant #989 : mieux vaut l'horloge qu'un tiret — c'est le
    // chiffre d'avant, pas une information perdue.
    rendreKanban([
      tacheFactice({ usage: usageFactice({ duree_ms: 42_000 }) }),
    ]);

    expect(screen.getByText("42 s")).toBeTruthy();
  });
});

// --- ② L'attente est un fait nommé, dans le détail ---------------------------

describe("le panneau de détail", () => {
  it("nomme chaque attente et la met en regard du travail", async () => {
    // La forme de la référence (GitLab CI, bloc de faits d'un job : « Durée »
    // puis « En file d'attente », étiquetées, empilées). Le travail vient en
    // tête : une attente seule ne se rapporte à rien.
    rendreKanban([
      tacheFactice({
        usage: usageQuiAAttendu({
          duree_attente_creneau_ms: 30_000,
          duree_attente_ms: 788_000,
        }),
      }),
    ]);
    await ouvrirLeDetail();

    const bloc = screen.getByRole("region", { name: "Temps" });
    expect(within(bloc).getByText("Travail")).toBeTruthy();
    expect(within(bloc).getByText("1 min 01 s")).toBeTruthy();
    expect(within(bloc).getByText("File d'attente de l'agent")).toBeTruthy();
    expect(within(bloc).getByText("30 s")).toBeTruthy();
    expect(within(bloc).getByText("Atelier du projet occupé")).toBeTruthy();
    expect(within(bloc).getByText("12 min 38 s")).toBeTruthy();
  });

  it("garde les attentes dans l'ordre du moteur, pas dans celui des durées", () => {
    // Trié par valeur, un lecteur ne saurait plus **à quel moment** la tâche a
    // attendu : le créneau vient avant l'atelier, qui vient avant l'arbitrage.
    const tache = tacheFactice({
      usage: usageQuiAAttendu({
        duree_attente_creneau_ms: 1_000,
        duree_arbitrage_ms: 5_000,
      }),
    });

    expect(attentesDe(tache).map((a) => a.libelle)).toEqual([
      "File d'attente de l'agent",
      "Atelier du projet occupé",
      "Décision humaine attendue",
    ]);
  });

  it("tait une attente mesurée à zéro", () => {
    // La règle du dépôt, déjà écrite pour l'arbitrage (#584) : annoncer « 0 s »
    // sur chacune des tâches d'un run apprendrait à ne plus lire la ligne.
    const tache = tacheFactice({ usage: usageQuiAAttendu() });
    expect(attentesDe(tache).map((a) => a.libelle)).toEqual([
      "Atelier du projet occupé",
    ]);
  });
});

// --- ③ Une tâche qui n'a rien attendu rend la carte d'avant ------------------

describe("une tâche qui n'a rien attendu", () => {
  it("n'ouvre aucun bloc de temps, et donc aucun panneau", async () => {
    // Le critère de #251 que ce ticket n'a pas le droit de défaire : une tâche
    // sans détail reste l'objet dense qu'on lit en diagonale. Répéter dans un
    // panneau la durée que la carte porte déjà l'ouvrirait sur chaque tâche
    // mesurée, pour rien.
    rendreKanban([
      tacheFactice({
        usage: usageFactice({
          duree_ms: 61_000,
          duree_execution_ms: 61_000,
          duree_attente_creneau_ms: 0,
          duree_attente_atelier_ms: 0,
          duree_attente_ms: 0,
        }),
      }),
    ]);

    expect(screen.queryByRole("button", { name: /Ouvrir le détail/ })).toBeNull();
    const utilisateur = userEvent.setup();
    await utilisateur.click(screen.getByRole("article"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("ne rend le bloc ouvrable que par ses attentes", () => {
    const sansAttente = tacheFactice({
      usage: usageFactice({ duree_ms: 61_000, duree_execution_ms: 61_000 }),
    });
    const avecAttente = tacheFactice({ usage: usageQuiAAttendu() });

    expect(detailDe(sansAttente).vide).toBe(true);
    expect(detailDe(avecAttente).vide).toBe(false);
  });

  it("n'invente aucune attente quand la tâche n'a pas d'usage", () => {
    expect(attentesDe(tacheFactice({ usage: null })).length).toBe(0);
  });
});

// --- ④ Le grand livre : le travail par tâche, le mur pour le run -------------

describe("le grand livre d'un run", () => {
  it("affiche le travail de chaque tâche", () => {
    render(
      <PanneauCouts
        couts={[
          coutExecutionFactice({
            taches: [coutTacheFactice({ usage: usageQuiAAttendu() })],
          }),
        ]}
      />,
    );

    expect(screen.getAllByText("1 min 01 s").length).toBeGreaterThan(0);
    expect(screen.queryByText("13 min 39 s")).toBeNull();
  });

  it("rend la durée du run telle que le backend l'a unie", () => {
    // La durée d'un run est l'union des intervalles de ses tâches, posée côté
    // serveur (`RunCost.duree_mur_ms`) : deux tâches menées de front ne
    // l'occupent qu'une fois. L'écran la rend, il ne la recalcule pas — c'est le
    // partage de GitHub Actions, dont l'en-tête d'un run porte « Total duration »
    // quand la somme de ses jobs vit ailleurs, sous un autre nom.
    render(
      <PanneauCouts
        couts={[
          coutExecutionFactice({
            total: usageFactice({
              duree_ms: 90_000,
              duree_execution_ms: 90_000,
              cout_usd: 0.2,
            }),
            taches: [
              coutTacheFactice({
                tache_id: "T-1",
                usage: usageFactice({ duree_ms: 60_000, duree_execution_ms: 60_000 }),
              }),
              coutTacheFactice({
                tache_id: "T-2",
                usage: usageFactice({ duree_ms: 60_000, duree_execution_ms: 60_000 }),
              }),
            ],
          }),
        ]}
      />,
    );

    // 1 min 30 s, et non les 2 min que la somme annoncerait.
    expect(screen.getAllByText("1 min 30 s").length).toBeGreaterThan(0);
    expect(screen.queryByText("2 min 00 s")).toBeNull();
  });
});

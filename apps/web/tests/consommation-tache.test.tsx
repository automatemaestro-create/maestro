/**
 * **Ce qu'une tâche a consommé se lit en contexte et en coût, le détail ventile le
 * cache** (#1398).
 *
 * Le défaut : la carte affichait `tokens_total` seul — la somme des contextes de
 * chacun des tours, cache relu compris. Sur p5, `socle-nextjs` y montrait
 * 1 845 438 tokens pour un contexte de ≈ 66 k et 1,87 $, et la personne y lisait
 * une tâche qui consommait trop. Sur le banc (passage 20261011-081931, S2),
 * `creer-app-py` : 90 585 tokens affichés, contexte atteint 16 074, dont 67 739
 * relus en cache.
 *
 * **La forme retenue** (variante C, choisie par le regard neuf sur trois variantes
 * rendues sur la vraie stack, contre LangSmith et Langfuse capturés et la barre
 * d'état de Claude Code) : la carte dit le **contexte atteint**, le coût garde sa
 * place ; le **panneau de détail** ventile — le cache en parts de l'entrée.
 *
 * Ce que ces tests gardent, et qui est exactement ce qui peut se défaire :
 *
 * 1. la carte n'affiche plus le volume seul : elle dit le contexte, et le coût ;
 * 2. le détail ventile contexte, entrée, cache relu et écrit (en parts), sortie,
 *    volume traité ;
 * 3. un contexte inconnu ne se fabrique pas : la carte retombe sur le volume,
 *    **nommé** « tokens traités », et le détail dit « non mesuré » ;
 * 4. le plafond en tokens dit ce qu'il compte : hors cache relu ;
 * 5. la sonde du point de contrôle a sa ligne au grand livre du run.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Kanban } from "@/components/Kanban";
import { PanneauCouts } from "@/components/PanneauCouts";
import { AUCUNE_BORNE, bornesEnLigne } from "@/lib/bornes";
import { formatTokens } from "@/lib/format";

import {
  agentFactice,
  coutExecutionFactice,
  projetFactice,
  tacheFactice,
  usageFactice,
} from "./aides";

/** L'usage réel de `creer-app-py` sur le banc, tel que l'API le sert. */
function usageServiParLeCache(partiel = {}) {
  return usageFactice({
    appels: 1,
    tokens_entree: 87_626,
    tokens_sortie: 2_959,
    tokens_total: 90_585,
    cout_usd: 0.2318,
    tours: 8,
    tokens_cache_lus: 67_739,
    tokens_cache_ecrits: 19_875,
    contexte_max: 16_074,
    tokens_hors_cache_lu: 22_846,
    ...partiel,
  });
}

function rendreKanban(taches: ReturnType<typeof tacheFactice>[]) {
  render(
    <Kanban
      taches={taches}
      agents={[agentFactice({ nom: "dev", role: "Développeur" })]}
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
  return screen.getByRole("region", { name: "Consommation" });
}

/**
 * Le texte que la carte **montre** : son texte entier moins les bulles, qui sont
 * dans le DOM au repos (masquées) pour la lecture d'écran.
 */
function texteVisible(element: HTMLElement): string {
  const copie = element.cloneNode(true) as HTMLElement;
  copie.querySelectorAll('[role="tooltip"]').forEach((bulle) => bulle.remove());
  return copie.textContent ?? "";
}

/** Les lignes d'un bloc de ventilation, « libellé = valeur », dans l'ordre. */
function lignes(bloc: HTMLElement): string[] {
  return within(bloc)
    .getAllByRole("term")
    .map((terme) => `${terme.textContent} = ${terme.nextElementSibling?.textContent}`);
}

// --- ① La carte dit le contexte et le coût, plus le volume seul ---------------

describe("la carte du Kanban", () => {
  it("dit le contexte atteint, et non la somme des contextes de chaque tour", () => {
    rendreKanban([
      tacheFactice({ usage: usageServiParLeCache(), cout_usd: 0.2318 }),
    ]);

    const visible = texteVisible(screen.getByRole("article"));
    expect(visible).toContain(`contexte ${formatTokens(16_074)} tokens`);
    // Le chiffre que la carte annonçait avant ce ticket ne s'affiche plus seul.
    expect(visible).not.toContain(formatTokens(90_585));
    // Le coût garde sa place.
    expect(visible).toContain("0,23");
  });

  it("garde le volume dans l'infobulle, nommé, en une ligne pas plus longue qu'avant", () => {
    // Une bulle plus haute est rognée par la colonne du Kanban qui défile (vu à
    // la relecture visuelle) : la ventilation vit dans le détail.
    rendreKanban([tacheFactice({ usage: usageServiParLeCache() })]);

    const bulle = screen.getByRole("tooltip");
    expect(bulle.textContent).toBe(`${formatTokens(90_585)} tokens traités en 8 tours`);
    expect((bulle.textContent ?? "").length).toBeLessThanOrEqual(
      `${formatTokens(87_626)} tokens en entrée / ${formatTokens(2_959)} en sortie`.length,
    );
  });

  it("ne fabrique pas de contexte inconnu : le volume, nommé pour ce qu'il est", () => {
    // Une mesure d'avant #1398, ou un fournisseur qui ne dit rien par appel.
    rendreKanban([
      tacheFactice({
        usage: usageFactice({ tokens_entree: 80_000, tokens_sortie: 5_000, tokens_total: 85_000 }),
      }),
    ]);

    const visible = texteVisible(screen.getByRole("article"));
    expect(visible).toContain(`${formatTokens(85_000)} tokens traités`);
    expect(visible).not.toContain("contexte");
  });
});

// --- ② Le détail ventile le cache, en parts de l'entrée -----------------------

describe("le panneau de détail", () => {
  it("ventile contexte, entrée et ses parts de cache, sortie et volume traité", async () => {
    rendreKanban([tacheFactice({ usage: usageServiParLeCache() })]);

    const bloc = await ouvrirLeDetail();

    expect(within(bloc).getByText("en tokens")).toBeTruthy();
    expect(lignes(bloc)).toEqual([
      `Contexte atteint = ${formatTokens(16_074)}`,
      `Entrée = ${formatTokens(87_626)}`,
      `dont relu en cache = ${formatTokens(67_739)}`,
      `dont écrit en cache = ${formatTokens(19_875)}`,
      `Sortie = ${formatTokens(2_959)}`,
      `Traités en 8 tours = ${formatTokens(90_585)}`,
    ]);
  });

  it("range le cache en retrait, comme une part et non comme un total de plus", async () => {
    rendreKanban([tacheFactice({ usage: usageServiParLeCache() })]);

    const bloc = await ouvrirLeDetail();

    const relu = within(bloc).getByText("dont relu en cache");
    expect(relu.className).toContain("pl-3");
    expect(relu.className).toContain("text-texte-secondaire");
    // Le contexte, chiffre qu'on suit, est accentué par la graisse.
    expect(within(bloc).getByText("Contexte atteint").className).toContain("font-medium");
  });

  it("dit « non mesuré » et tait le cache d'une mesure qui ne le rapporte pas", async () => {
    rendreKanban([
      tacheFactice({
        usage: usageFactice({ tokens_entree: 80_000, tokens_sortie: 5_000, tokens_total: 85_000, tours: 3 }),
      }),
    ]);

    const bloc = await ouvrirLeDetail();

    expect(lignes(bloc)).toEqual([
      "Contexte atteint = non mesuré",
      `Entrée = ${formatTokens(80_000)}`,
      `Sortie = ${formatTokens(5_000)}`,
      `Traités en 3 tours = ${formatTokens(85_000)}`,
    ]);
  });
});

// --- ③ Le plafond en tokens dit ce qu'il compte -------------------------------

describe("le plafond en tokens", () => {
  it("se dit hors cache relu, là où les bornes d'un run se relisent", () => {
    // La décision du ticket : le plafond compte les tokens neufs, pas ce que le
    // modèle relit de son cache à chaque tour — et l'écran le dit avec le chiffre.
    expect(bornesEnLigne({ ...AUCUNE_BORNE, plafond_tokens: 500_000 })).toBe(
      `s'interrompt à ${formatTokens(500_000)} tokens hors cache relu`,
    );
  });
});

// --- ④ La sonde a sa ligne au grand livre du run ------------------------------

describe("le grand livre", () => {
  it("donne sa ligne à la sonde du point de contrôle quand elle a coûté", () => {
    render(
      <PanneauCouts
        couts={[
          coutExecutionFactice({
            sonde: usageFactice({ appels: 1, tokens_entree: 2_285, tokens_sortie: 118, tokens_total: 2_403, cout_usd: 0.0059 }),
          }),
        ]}
      />,
    );

    const ligne = screen.getByText("Sonde du point de contrôle").closest("tr");
    expect(ligne).not.toBeNull();
    expect(texteVisible(ligne as HTMLElement)).toContain(formatTokens(2_403));
  });

  it("n'en ouvre aucune pour un run sans sonde ou un grand livre d'avant #1398", () => {
    render(<PanneauCouts couts={[coutExecutionFactice()]} />);

    expect(screen.queryByText("Sonde du point de contrôle")).toBeNull();
  });
});

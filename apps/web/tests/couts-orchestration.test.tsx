/**
 * **L'orchestration n'est pas une part d'agent** (#1028) — et l'écran des coûts
 * le dit par sa mise en page, pas par un compteur corrigé.
 *
 * Le défaut, constat **C13** du retex du 2026-09-11 : le tableau de bord
 * annonçait « 6 agent(s) du poste », `/agents` en listait 5, et `/couts` donnait
 * 21 % de la dépense à `orchestrateur` **comme à un agent**. La question de fond
 * — *l'orchestrateur est-il un agent ?* — était déjà tranchée dans le dépôt
 * ([docs/37 §4.2](../../../docs/37-decision-equipe-sur-mesure.md), 2026-09-19) :
 * il ne l'est pas, il est Maestro.
 *
 * **La forme retenue** (variante C, choisie par le regard neuf sur trois
 * variantes rendues, contre Argo CD et GitHub Actions capturés en direct) :
 * l'orchestration se lit **dans l'en-tête du bloc** — « dont orchestration … » —
 * et la liste ne contient plus que des agents. Ce qui la distingue est sa
 * **place** et son **mot** ; les deux variantes qui confiaient la distinction à
 * un signe graphique — barre absente, barre creuse — ont été écartées parce
 * qu'elles se lisent comme un défaut de rendu.
 *
 * Ce que ces tests gardent, et qui est exactement ce qui peut se défaire :
 *
 * 1. l'orchestration **n'a pas de ligne** dans la liste des agents ;
 * 2. elle est **nommée** en en-tête, avec son montant et sa part ;
 * 3. les parts se comptent sur le **tout** — l'étiquette et la liste annoncent
 *    des pourcentages du même dénominateur, et ils font 100 % ensemble ;
 * 4. rien de mesuré ≠ zéro : sans poste d'orchestration, l'en-tête ne rend rien
 *    plutôt qu'un « 0,00 $US » qui affirmerait qu'elle n'a rien coûté ;
 * 5. les deux vides **distinguent** ce qui est vide — « aucun usage » quand
 *    rien n'a dépensé, « aucun agent n'a travaillé » quand seule
 *    l'orchestration a dépensé. Le libellé d'avant #1028 (« aucun usage
 *    attribué **à un agent** ») ne parlait que d'une population : depuis qu'il
 *    y en a deux, il taisait la seconde. Les deux tiennent sur **une ligne**,
 *    la carte partageant sa rangée avec « Évolution du coût ».
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  EtiquetteOrchestration,
  RepartitionAgents,
} from "@/components/RepartitionAgents";
import type { CoutAgentAgrege } from "@/lib/types";

import { usageFactice } from "./aides";

function poste(
  agent: string,
  cout: number,
  role = "",
  tokens = 1000,
): CoutAgentAgrege {
  return {
    agent,
    role,
    taches: 0,
    usage: usageFactice({ cout_usd: cout, tokens_total: tokens, appels: 1 }),
  };
}

/** Le parc de la démo : trois agents pour 0,79 $ — et Maestro pour 0,21 $. */
const AGENTS = [
  poste("developpeur", 0.5, "Développeur"),
  poste("bdd", 0.2, "Base de données"),
  poste("qa", 0.09, "QA / Testeur"),
];
const ORCHESTRATION = poste("orchestrateur", 0.21, "Orchestrateur");

describe("la répartition des coûts et l'orchestration", () => {
  it("ne donne aucune ligne d'agent à l'orchestration", () => {
    render(
      <RepartitionAgents agents={AGENTS} orchestration={ORCHESTRATION} />,
    );

    expect(screen.getByText("developpeur")).toBeInTheDocument();
    // Le nom technique n'apparaît nulle part dans la liste : c'est le point du
    // ticket. Le voir ici signifierait qu'on l'a remis parmi les exécutants.
    expect(screen.queryByText("orchestrateur")).not.toBeInTheDocument();
  });

  it("la nomme en en-tête, avec son montant et sa part", () => {
    render(
      <EtiquetteOrchestration agents={AGENTS} orchestration={ORCHESTRATION} />,
    );

    const etiquette = screen.getByText(/dont/);
    expect(etiquette).toHaveTextContent("orchestration");
    expect(etiquette).toHaveTextContent("0,21");
    expect(etiquette).toHaveTextContent("21");
  });

  it("compte les parts sur le tout, orchestration comprise", () => {
    // La garde du chiffre qui ment : recomptées sur le parc seul, les trois
    // agents feraient 63/25/11 % et l'écran dirait qu'ils ont dépensé toute la
    // fenêtre. Sur le tout, ils font 50/20/9 % — et les 21 % qui manquent sont
    // précisément ce que l'en-tête annonce.
    const { container } = render(
      <RepartitionAgents agents={AGENTS} orchestration={ORCHESTRATION} />,
    );

    expect(container).toHaveTextContent("50 %");
    expect(container).toHaveTextContent("20 %");
    expect(container).toHaveTextContent("9 %");
  });

  it("ne rend rien en en-tête quand l'orchestration n'a rien coûté de mesuré", () => {
    const { container } = render(
      <EtiquetteOrchestration agents={AGENTS} orchestration={null} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("distingue les deux vides, sans restreindre au parc", () => {
    const sansRien = render(<RepartitionAgents agents={[]} />);
    expect(sansRien.container).toHaveTextContent("Aucun usage sur la période.");
    // Le libellé d'avant #1028 restreignait à une population sur deux : le
    // remettre ferait taire l'orchestration dans l'état où l'on vérifie
    // justement qu'elle n'a rien dépensé.
    expect(sansRien.container).not.toHaveTextContent("à un agent");
    sansRien.unmount();

    // L'autre vide, que ce même libellé aurait décrit à l'envers : quelque
    // chose a bien été dépensé, simplement pas par le parc — et l'en-tête le
    // chiffre juste au-dessus.
    const sansAgent = render(
      <RepartitionAgents agents={[]} orchestration={ORCHESTRATION} />,
    );
    expect(sansAgent.container).toHaveTextContent(
      "Aucun agent n'a travaillé sur la période.",
    );
  });

  it("garde la liste intacte quand il n'y a pas d'orchestration", () => {
    // Le repli : une fenêtre sans dépense de cadrage (ou un backend d'avant
    // #1028) rend exactement la liste d'avant, parts recomptées sur le parc.
    const { container } = render(<RepartitionAgents agents={AGENTS} />);

    const lignes = within(container).getAllByText(/%/);
    expect(lignes).toHaveLength(3);
    expect(container).toHaveTextContent("63 %");
  });
});

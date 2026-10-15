/**
 * **Ce que le fil dépense se voit dans Coûts & analytics** (#1534) — rangé à part
 * du run, comme l'orchestration (#1028).
 *
 * Le constat de #1508 (docs/50 § 1.2) : sur le projet de p5, 1,548 $ dépensés en
 * conversation — la déclaration du projet, ses questions d'outillage, ses pièces,
 * son équipe, ses récits — n'entraient dans aucun total. L'API les sert désormais
 * sous un poste `fil` ; l'écran les dit **à la place de l'orchestration et avec
 * son mot**, sans forme nouvelle : « dont orchestration …, fil … ».
 *
 * Ce que ces tests gardent :
 *
 * 1. le fil est **nommé** en en-tête, après l'orchestration, avec son montant et
 *    sa part — et il l'est seul quand l'orchestration n'a rien coûté ;
 * 2. les parts se comptent sur le **tout**, fil compris : la liste des agents et
 *    l'en-tête annoncent des pourcentages du même dénominateur ;
 * 3. le fil n'a **pas de ligne** parmi les agents ;
 * 4. le fil d'activité dit ce qu'une dépense du fil a payé, en toutes lettres.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  EtiquetteOrchestration,
  RepartitionAgents,
} from "@/components/RepartitionAgents";
import { resumeEvenement } from "@/lib/evenements";
import { EVENEMENT_FIL_DEPENSE, type CoutAgentAgrege } from "@/lib/types";

import { evenementFactice, usageFactice } from "./aides";

function poste(agent: string, cout: number, role = ""): CoutAgentAgrege {
  return {
    agent,
    role,
    taches: 0,
    usage: usageFactice({ cout_usd: cout, tokens_total: 1000, appels: 1 }),
  };
}

/** Deux agents pour 0,60 $, l'orchestration du run pour 0,10 $, le fil pour 0,30 $. */
const AGENTS = [poste("developpeur", 0.4, "Développeur"), poste("qa", 0.2, "QA")];
const ORCHESTRATION = poste("orchestrateur", 0.1, "Orchestrateur");
const FIL = poste("fil", 0.3);

describe("la répartition des coûts et le fil", () => {
  it("nomme le fil en en-tête, après l'orchestration, avec son montant et sa part", () => {
    render(
      <EtiquetteOrchestration agents={AGENTS} orchestration={ORCHESTRATION} fil={FIL} />,
    );

    const etiquette = screen.getByText(/dont/);
    expect(etiquette).toHaveTextContent(/orchestration.*0,10.*10\s%.*fil.*0,30.*30\s%/);
  });

  it("nomme le fil seul quand l'orchestration n'a rien coûté de mesuré", () => {
    render(<EtiquetteOrchestration agents={AGENTS} orchestration={null} fil={FIL} />);

    const etiquette = screen.getByText(/dont/);
    expect(etiquette).not.toHaveTextContent("orchestration");
    expect(etiquette).toHaveTextContent(/fil.*0,30/);
  });

  it("compte les parts des agents sur le tout, fil compris", () => {
    // Sur le parc seul, ils feraient 67/33 % ; avec l'orchestration seule, 57/29 %.
    // Sur le tout, 40/20 % — les 40 % restants sont ceux que l'en-tête annonce.
    const { container } = render(
      <RepartitionAgents agents={AGENTS} orchestration={ORCHESTRATION} fil={FIL} />,
    );

    expect(container).toHaveTextContent("40 %");
    expect(container).toHaveTextContent("20 %");
    expect(screen.queryByText("fil")).not.toBeInTheDocument();
  });

  it("dit que des agents n'ont rien fait quand seul le fil a dépensé", () => {
    const { container } = render(<RepartitionAgents agents={[]} fil={FIL} />);
    expect(container).toHaveTextContent("Aucun agent n'a travaillé sur la période.");
  });

  it("dit dans le fil d'activité ce qu'une dépense du fil a payé", () => {
    // Le montant est lu dans `detail`, composé par le backend : relu du journal
    // requêtable, qui ne garde pas `cout_usd`, l'événement dit encore combien.
    const relu = evenementFactice({
      type: EVENEMENT_FIL_DEPENSE,
      titre: "déclaration du projet",
      detail: "0,4200 $ · 12 480 tokens",
      cout_usd: null,
      usage: null,
    });
    expect(resumeEvenement(relu)).toBe(
      "Le fil a dépensé pour déclaration du projet : 0,4200 $ · 12 480 tokens",
    );
  });
});

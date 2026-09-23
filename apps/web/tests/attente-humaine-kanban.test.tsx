/**
 * **Une tâche qui attend un humain ne se dit nulle part « au travail »** (#1111).
 *
 * Le défaut, relevé au bouclage du jalon « L'atelier » le 2026-09-21 : sur le
 * run `demo-live`, la tâche « Pipeline CI et déploiement de l'API » attend une
 * validation, et chaque vue en disait autre chose — le pipeline « Attente
 * humaine », l'en-tête « Validation en attente », le tableau de bord
 * « Suspendus », et le Kanban « En cours · Travaille depuis 1 min ». Les
 * **comptes** concordaient partout (1 en cours) ; les **mots** non.
 *
 * Ce que ces tests gardent, et qui est exactement ce qui peut se défaire :
 *
 * 1. **la colonne ne bouge pas** — une tâche arrêtée sur un humain reste dans
 *    « En cours », qui reste à 1. C'est la contrainte écrite du ticket (les
 *    colonnes suivent la machine à états, les comptes restent ceux de #924), et
 *    c'est aussi ce que le moteur impose : il n'émet pas encore
 *    `en_attente_validation`, et `progression.py` compte cette tâche en vol ;
 * 2. **les trois façons de dire « au travail » se taisent** — le mot « En
 *    cours », le signe de vie (dernier geste) et le chrono en vol
 *    (« Travaille depuis… ») ;
 * 3. **la carte dément sa colonne** — surface `attention` et geste qui tranche,
 *    qui est la variante retenue sur pièces (commentaire « ## Variante
 *    retenue » du ticket). ⚠ Ce geste était un **lien** vers `/validations`
 *    jusqu'à #1228, qui en fait un **bouton** : on tranche sur place et l'on
 *    reste où l'on est. La prémisse d'origine — un arbitrage ne se *lit* pas
 *    dans une carte de 11 rem — n'a pas changé ; ce qui a changé est qu'on n'a
 *    plus besoin de quitter l'écran pour le lire (`PanneauValidation`) ;
 * 4. **rien ne bouge sur les autres cartes**, ni quand personne ne passe la
 *    liste des attentes : un Kanban monté sans elle rend la carte d'avant.
 *
 * La règle elle-même (`lib/execution.tacheArreteeSurUnHumain`) est éprouvée
 * hors rendu, parce que c'est **une** question posée par deux vues — le nœud de
 * pipeline la pose depuis #491, la carte depuis ce ticket — et que la recopier
 * serait le défaut qu'elle existe pour éviter.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ArbitrageSurPlace } from "@/components/CarteValidation";
import { Kanban } from "@/components/Kanban";
import {
  tacheArreteeSurUnHumain,
  tachesEnAttenteDeValidation,
} from "@/lib/execution";
import { arbitragesEnAttente } from "@/lib/validations";
import {
  STATUT_EN_ATTENTE_VALIDATION,
  VALIDATION_APPROUVEE,
  type SigneDeVie,
} from "@/lib/types";

import {
  agentFactice,
  projetFactice,
  tacheFactice,
  validationFactice,
} from "./aides";

/**
 * Le geste et le départ de travail d'une tâche en vol — les deux temps de #894,
 * qui sont précisément ce que la carte doit taire quand l'attente commence.
 */
const GESTE: SigneDeVie = {
  horodatage: "2026-09-21T15:24:00+00:00",
  libelle: "Écrit .github/workflows/ci.yml, puis relit le résultat",
  travaille_depuis: "2026-09-21T15:23:00+00:00",
};

/** La tâche du relevé : en cours pour le moteur, arrêtée sur un humain en vrai. */
function tacheArretee() {
  return tacheFactice({
    id: "demo-t3",
    titre: "Pipeline CI et déploiement de l'API",
    statut: "en_cours",
    activite: GESTE,
  });
}

function rendreKanban(
  taches = [tacheArretee()],
  enAttenteHumaine?: ReadonlySet<string>,
  arbitrer?: ArbitrageSurPlace,
) {
  return render(
    <Kanban
      taches={taches}
      agents={[agentFactice({ nom: "infra", role: "DevOps" })]}
      reassigner={vi.fn()}
      projet={projetFactice()}
      enAttenteHumaine={enAttenteHumaine}
      arbitrer={arbitrer}
    />,
  );
}

/** De quoi trancher la demande de `demo-t3` sur place (#1228). */
function arbitrageDe(decider = vi.fn()): ArbitrageSurPlace {
  return {
    enAttente: arbitragesEnAttente([
      validationFactice({ tache_id: "demo-t3", outil: "Bash" }),
    ]),
    decider,
  };
}

/** La colonne d'un statut, repérée par son titre — même repère que `kanban.test`. */
const colonne = (titre: string) =>
  screen.getByRole("heading", { name: new RegExp(`^${titre}`) })
    .parentElement as HTMLElement;

const carteDe = (titre: string) =>
  screen.getByText(titre).closest("article") as HTMLElement;

// --- ① La colonne et son compte ne bougent pas ------------------------------

describe("une tâche arrêtée sur un humain, au Kanban", () => {
  it("reste dans la colonne « En cours », qui reste à 1", () => {
    rendreKanban([tacheArretee()], new Set(["demo-t3"]));

    const enCours = colonne("En cours");
    expect(
      within(enCours).getByText("Pipeline CI et déploiement de l'API"),
    ).toBeTruthy();
    // Le compte de l'en-tête de colonne : celui de #924, inchangé.
    expect(within(enCours).getByText("1")).toBeTruthy();
    // Et aucune colonne ne naît pour l'occasion.
    expect(
      screen.queryByRole("heading", { name: /^Attente humaine/ }),
    ).toBeNull();
  });

  // --- ② Les trois façons de dire « au travail » se taisent ----------------

  it("ne dit plus « En cours » sur la carte, mais « Attente humaine »", () => {
    rendreKanban([tacheArretee()], new Set(["demo-t3"]));

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(within(carte).getByText("Attente humaine")).toBeTruthy();
    expect(within(carte).queryByText("En cours")).toBeNull();
  });

  it("tait le signe de vie — une tâche arrêtée ne « bouge » pas", () => {
    rendreKanban([tacheArretee()], new Set(["demo-t3"]));

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(carte.querySelector("[data-signe-de-vie]")).toBeNull();
    expect(within(carte).queryByText(GESTE.libelle)).toBeNull();
  });

  it("tait le chrono en vol, dont le mot était « Travaille »", () => {
    rendreKanban([tacheArretee()], new Set(["demo-t3"]));

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(carte.querySelector("[data-chrono-en-vol]")).toBeNull();
    expect(within(carte).queryByText(/Travaille/)).toBeNull();
  });

  // --- ③ La carte dément sa colonne ----------------------------------------

  it("prend la surface d'attention et porte le geste qui tranche", () => {
    rendreKanban([tacheArretee()], new Set(["demo-t3"]), arbitrageDe());

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    // L'ambre que `Carte ton="attention"` accorde déjà au nœud de pipeline —
    // la classe, pas la couleur : jsdom ne calcule aucun rendu.
    expect(carte.className).toMatch(/amber/);

    // ⚠ **Un bouton, plus un lien** (#1228). C'est l'assertion qui garde le
    // renversement : le lien menait à `/validations` et y laissait sans retour.
    expect(within(carte).getByRole("button", { name: "Trancher" })).toBeTruthy();
    expect(within(carte).queryByRole("link", { name: /Trancher/ })).toBeNull();
  });

  it("n'offre aucun geste quand l'écran ne passe pas de quoi trancher", () => {
    // Le Kanban du tableau de bord, par exemple : la carte dit toujours qu'une
    // tâche attend quelqu'un, elle ne fabrique pas un bouton sans demande.
    rendreKanban([tacheArretee()], new Set(["demo-t3"]));

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(within(carte).getByText("Attente humaine")).toBeTruthy();
    expect(within(carte).queryByRole("button", { name: "Trancher" })).toBeNull();
  });
});

// --- ④ Rien ne bouge ailleurs ----------------------------------------------

describe("ce qui ne bouge pas", () => {
  it("laisse intacte la carte d'une tâche qui travaille vraiment", () => {
    rendreKanban([tacheArretee()], new Set(["une-autre-tache"]));

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(within(carte).getByText("En cours")).toBeTruthy();
    expect(carte.querySelector("[data-signe-de-vie]")).not.toBeNull();
    expect(carte.querySelector("[data-chrono-en-vol]")).not.toBeNull();
    expect(within(carte).queryByRole("button", { name: "Trancher" })).toBeNull();
    expect(carte.className).not.toMatch(/amber/);
  });

  it("rend la carte d'avant quand personne ne passe les attentes", () => {
    // Le Kanban se monte aussi hors de la vue d'un run, où les validations du
    // projet ne sont pas à portée : sans la liste, il ne devine rien.
    rendreKanban([tacheArretee()]);

    const carte = carteDe("Pipeline CI et déploiement de l'API");
    expect(within(carte).getByText("En cours")).toBeTruthy();
    expect(carte.querySelector("[data-chrono-en-vol]")).not.toBeNull();
  });
});

// --- La règle, hors rendu ---------------------------------------------------

describe("la question « cette tâche est-elle arrêtée sur quelqu'un ? »", () => {
  it("répond oui sur la file des validations — la source qui existe", () => {
    const attentes = tachesEnAttenteDeValidation([
      validationFactice({ tache_id: "demo-t3" }),
    ]);

    expect(tacheArreteeSurUnHumain(tacheArretee(), attentes)).toBe(true);
  });

  it("répond oui sur le statut — la source qui existera", () => {
    // Le moteur n'émet pas encore `en_attente_validation` ; le jour où il le
    // fera, la carte ne doit pas attendre qu'une demande traîne dans la file.
    const tache = tacheFactice({ statut: STATUT_EN_ATTENTE_VALIDATION });

    expect(tacheArreteeSurUnHumain(tache, new Set())).toBe(true);
  });

  it("répond non quand la demande a été tranchée", () => {
    // Une validation approuvée a rendu la tâche au moteur : elle ne retient
    // plus rien, et la carte doit se remettre à compter le travail.
    const attentes = tachesEnAttenteDeValidation([
      validationFactice({ tache_id: "demo-t3", statut: VALIDATION_APPROUVEE }),
    ]);

    expect(tacheArreteeSurUnHumain(tacheArretee(), attentes)).toBe(false);
  });
});

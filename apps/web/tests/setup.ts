/**
 * La mise en scène commune à tous les tests de la Control Tower (#124).
 *
 * Ce que jsdom ne donne pas et dont la refonte UX dépend :
 *
 * 1. **`window.matchMedia`** — jsdom ne l'implémente pas du tout. Or le thème
 *    (#118) s'y adosse pour résoudre le choix « système », et il ne suffit pas
 *    d'un stub qui répond toujours « clair » : le mode « système » doit
 *    continuer de suivre l'OS quand celui-ci bascule en cours de session, ce
 *    qui suppose de pouvoir déclencher l'événement `change`. D'où le double
 *    `poserPreferenceSysteme` / `basculerPreferenceSysteme` de `aides.tsx`.
 * 2. **`ResizeObserver` et `scrollIntoView`** — absents de jsdom, et appelés
 *    respectivement par la réserve de défilement des Paramètres (#121) et par
 *    la visite guidée (#122), qui amène chaque ancre à l'écran. Sans eux, ces
 *    composants lèvent au montage.
 * 3. **Un état propre entre deux tests** — le `localStorage`, l'attribut
 *    `data-theme` du document et le DOM monté survivraient d'un test à l'autre
 *    et rendraient l'ordre d'exécution signifiant. Tout est remis à zéro ici,
 *    plutôt que dans chaque fichier.
 * 4. **Le réseau débranché** — `useControlTower` et `useChat` ouvrent un
 *    WebSocket et appellent l'API REST. C'est la plomberie des tickets #47 et
 *    #85, pas ce que la refonte a changé : les tests la remplacent globalement
 *    par un état immobile que chacun règle (`poserEtatGlobal`,
 *    `poserFilAssistance`), si bien qu'aucun test n'a besoin de backend ni de
 *    faux serveur. Le vrai va-et-vient réseau reste couvert de bout en bout par
 *    le skill `/verify`.
 */

import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import {
  cheminCourant,
  etatGlobalCourant,
  filAssistanceCourant,
  installerMatchMedia,
  navigations,
  poserChemin,
  poserEtatGlobal,
  poserFilAssistance,
  routeurFactice,
} from "./aides";

// Déclarés ici plutôt que dans chaque fichier : les hooks réseau ne sont jamais
// réels en test. Les mocks lisent l'état à CHAQUE rendu (et non une fois pour
// toutes), pour qu'un `poser…` suivi d'un nouveau rendu soit vu.
//
// Mock **partiel** (#249) : seul le hook est remplacé, le reste du module passe
// tel quel. Un module entièrement substitué perd ses autres exports — la page
// Journal lit `MAX_EVENEMENTS` pour dire combien d'événements elle garde, et
// tomberait ici sur « No "MAX_EVENEMENTS" export is defined on the mock » sans
// que rien, ni au build ni au lint, ne l'ait laissé prévoir.
vi.mock("@/lib/useControlTower", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useControlTower: () => etatGlobalCourant(),
}));

vi.mock("@/lib/useChat", () => ({
  useChat: () => filAssistanceCourant(),
}));

// Hors d'un routeur Next, `usePathname` et `useRouter` n'ont pas de contexte :
// la sidebar et la barre supérieure lisent le premier pour désigner la page
// courante, la visite guidée pousse sur le second pour amener l'utilisateur sur
// la page d'une étape.
vi.mock("next/navigation", () => ({
  usePathname: () => cheminCourant(),
  useRouter: () => routeurFactice(),
}));

class ResizeObserverFactice {
  observe() {}
  unobserve() {}
  disconnect() {}
}

beforeEach(() => {
  installerMatchMedia();
  poserEtatGlobal();
  poserFilAssistance();
  poserChemin("/");
  navigations.length = 0;
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.ResizeObserver = ResizeObserverFactice as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
  cleanup();
});

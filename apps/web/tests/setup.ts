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
 *    WebSocket et appellent l'API REST, la porte d'entrée du shell (#279) lit
 *    les projets déclarés et le fil d'activité part du journal persisté (#478).
 *    C'est la plomberie des tickets #47, #85, #223 et #478, pas ce que la
 *    refonte a changé : les tests la remplacent globalement par un état immobile
 *    que chacun règle (`poserEtatGlobal`, `poserFilAssistance`, `poserProjets`,
 *    `poserJournal`), si bien qu'aucun test n'a besoin de backend ni de faux
 *    serveur. Le vrai va-et-vient réseau reste couvert de bout en bout par le
 *    skill `/verify`.
 * 5. **De quoi attendre sur un runner chargé** — voir `asyncUtilTimeout`
 *    ci-dessous : le verdict de la suite ne doit pas dépendre de la charge de la
 *    machine qui la joue.
 */

import "@testing-library/jest-dom/vitest";

import { configure } from "@testing-library/dom";
import { cleanup } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";

import {
  CATALOGUE_POSTE_NU,
  canauxDemandes,
  catalogueAgents,
  cheminCourant,
  etatGlobalCourant,
  filAssistanceCourant,
  fournisseursDuPoste,
  installerMatchMedia,
  navigations,
  noterCanal,
  noterPortee,
  noterProjetDuFil,
  pageJournalCourante,
  porteesDemandees,
  poserCatalogueAgents,
  poserChemin,
  poserEtatGlobal,
  poserFilAssistance,
  poserFournisseurs,
  poserJournal,
  poserProjets,
  projetsDeclares,
  projetsDuFil,
  routeurFactice,
} from "./aides";

// Le budget d'attente des `findBy*`/`waitFor` — 1 s par défaut, ce qui est une
// mesure du *poste* et non du code. Sur le runner CI partagé, les 14 fichiers
// tournent en parallèle et une même assertion qui se résout ici en ~50 ms y
// demande 1 à 3,7 s : le premier test d'un fichier, qui paie en plus le premier
// rendu de son arbre, franchit la seconde et échoue seul, en laissant une
// capture DOM figée sur l'état de chargement (`projet-actif.test.tsx`, #279).
// C'est la règle générale du dépôt (docs/10 §8) : le verdict de la suite ne
// dépend pas de la machine qui la joue — donc on élargit l'attente au lieu de
// retoucher l'assertion, qui, elle, disait vrai. Ce n'est pas de la patience
// gratuite : rien n'attend jamais ce budget quand le rendu arrive, il ne coûte
// que sur un test déjà rouge.
configure({ asyncUtilTimeout: 5_000 });

// Déclarés ici plutôt que dans chaque fichier : les hooks réseau ne sont jamais
// réels en test. Les mocks lisent l'état à CHAQUE rendu (et non une fois pour
// toutes), pour qu'un `poser…` suivi d'un nouveau rendu soit vu.
//
// Mock **partiel** (#249) : seul le hook est remplacé, le reste du module passe
// tel quel. Un module entièrement substitué perd ses autres exports — un
// appelant qui lit `MAX_EVENEMENTS` à côté du hook tomberait ici sur « No
// "MAX_EVENEMENTS" export is defined on the mock » sans que rien, ni au build ni
// au lint, ne l'ait laissé prévoir.
// La **portée** reçue est notée au passage (#281) : le hook est factice, mais
// ce qu'on lui demande est précisément ce que ce lot promet — chaque écran ne
// lit que le projet actif. Sans cette note, la promesse ne serait observable
// nulle part, le contenu rendu venant de `poserEtatGlobal` quoi qu'il arrive.
vi.mock("@/lib/useControlTower", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useControlTower: (portee: string) => {
    noterPortee(portee);
    return etatGlobalCourant();
  },
}));

// Le **canal** demandé est noté au passage (#273), pour la raison qui vaut déjà
// pour la portée de `useControlTower` : le fil rendu est factice, mais celui
// qu'on demande est exactement ce que le chat global promet — une mention
// `@agent` change de destinataire au lieu de recopier le message (#269).
// Le **projet** passé au fil l'est aussi (#683) : c'est lui qui rattache le run
// que l'orchestration ouvre, donc ce qui décide qu'il figure — ou non — dans la
// liste des runs de la fenêtre où on l'a demandé.
vi.mock("@/lib/useChat", () => ({
  useChat: (canal: string, projetId: string | null = null) => {
    noterCanal(canal);
    noterProjetDuFil(projetId);
    return filAssistanceCourant();
  },
}));

// La porte d'entrée (#279) lit les projets déclarés à chaque montage du shell :
// sans ce mock, *tous* les tests qui rendent le shell tapent le réseau. Seule
// cette lecture est remplacée — `importOriginal` garde `ErreurProjet` **la**
// classe du module (sinon le `instanceof` qui distingue un refus motivé d'une
// panne réseau ne reconnaîtrait plus rien) et les autres routes intactes. Un
// fichier de test qui a besoin de plus mocke `@/lib/api` chez lui, ce qui prend
// alors le pas sur cette déclaration (`tests/projets.test.tsx`,
// `tests/projet-actif.test.tsx`).
//
// ⚠ Ce dernier point ne tient qu'à une condition : `@/lib/api` ne doit pas être
// **importé** par ce fichier ni par `./aides`, fût-ce en transitif. Un module
// déjà évalué garde le client réel, et le mock d'un fichier de test, enregistré
// plus tard, n'a plus rien à remplacer — les mocks locaux redeviennent alors
// silencieusement inopérants. C'est pour cela que la mémoire du projet actif
// (`lib/projetActif`) est séparée de sa résolution (`lib/etatProjetActif`) :
// `./aides` n'a besoin que de la première, qui ne dépend de rien.
// Le **journal persisté** (#478) rejoint cette liste pour la même raison : depuis
// que la page Journal et la vue d'un run partent de l'historique, les rendre
// sans ce mock taperait le réseau et laisserait l'écran figé sur sa lecture.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => Promise.resolve(projetsDeclares()),
    chargerJournal: () => Promise.resolve(pageJournalCourante()),
    // #487 : le formulaire d'agent demande au poste ce qui y est installé. Le
    // défaut est un **poste nu** — la sonde ne trouve rien, les deux champs
    // restent en saisie libre, et tout test écrit avant ce lot dit encore vrai.
    chargerFournisseurs: () => Promise.resolve(fournisseursDuPoste()),
    // #255 : le même formulaire lit les **rôles connus** dans le catalogue des
    // agents. Défaut vide — aucun rôle suggéré, le champ reste celui d'avant —,
    // et les 18 fichiers qui mockent `@/lib/api` chez eux gardent le pas sur
    // cette déclaration.
    chargerCatalogue: () => Promise.resolve(catalogueAgents()),
  };
});

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
  poserProjets([]);
  poserJournal([]);
  poserFournisseurs(CATALOGUE_POSTE_NU);
  poserCatalogueAgents([]);
  navigations.length = 0;
  porteesDemandees.length = 0;
  canauxDemandes.length = 0;
  projetsDuFil.length = 0;
  window.localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  window.ResizeObserver = ResizeObserverFactice as unknown as typeof ResizeObserver;
  Element.prototype.scrollIntoView = () => {};
});

afterEach(() => {
  cleanup();
});

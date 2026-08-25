/**
 * Le filet d'accessibilité de la Control Tower (#537, lot 5 de #532).
 *
 * Le travail d'accessibilité du produit est sérieux — 104 `aria-label`, 44 rôles
 * corrects, un `<h1>` par écran, 0 saut de niveau (docs/30 §2.1). Ce qui
 * manquait n'était pas de la rigueur, c'était **ce qui la garde** : `axe-core`
 * était dans le dépôt depuis toujours, en transitif, et n'avait jamais été
 * importé une seule fois (docs/30 §3.4).
 *
 * Ce fichier porte **deux** des trois critères du lot — le troisième, le passage
 * de `plugin:jsx-a11y/recommended` en `error`, vit dans `eslint.config.mjs` et
 * n'a pas de test à lui : c'est le lint qui rougit. L'ordre des `describe`
 * ci-dessous est celui de leur dépendance :
 *
 * 1. **la sonde est prouvée avant de servir** — sur un échantillon fautif, puis
 *    sur un fragment sain. Sans cette première moitié, un audit mal branché
 *    (mauvais contexte, règles toutes désactivées) rendrait un ✓ sur une
 *    question jamais posée. C'est la méthode de `contraste.test.ts` (#534), et
 *    c'est ce qui distingue un filet d'un instantané ;
 * 2. **les 10 écrans**, montés dans leur shell réel et audités — table dérivée
 *    de `MENU`, jamais recopiée : un écran ajouté au menu sans cas d'audit fait
 *    rougir, plutôt que de passer inaperçu ;
 * 3. **les acquis qu'axe ne sait pas voir** — le lien d'évitement (rendu), la
 *    garde de mouvement (balayage des sources) et la taille des cibles (rendu,
 *    sur les classes déclarées). Chacun est gardé là où il est observable, et
 *    aucun ne mesure un pixel : jsdom n'en calcule aucun, et c'est la frontière
 *    de #308 — le pixel appartient au skill `/banc-mise-en-page`.
 *
 * ⚠ Le réseau est débranché deux fois, et il faut les deux : `tests/setup.ts`
 * mocke `useControlTower`/`useChat`, mais **ni `chargerCatalogue`, ni
 * `chargerSante`, ni le pool MCP, ni l'explorateur** (piège documenté dans
 * `apps/web/README.md`). Un écran qui les lit partirait sur un vrai `fetch` et
 * n'offrirait à l'audit qu'une bannière d'erreur — donc un écran vert parce
 * qu'il est vide. Le mock local ci-dessous **remplace** celui du setup, d'où la
 * reconduction de `chargerProjets`/`chargerJournal`.
 */

import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ID_CONTENU_PRINCIPAL, Shell } from "@/components/Shell";
import { ListeAgents } from "@/components/ListeAgents";
import { ongletAgentOuDefaut } from "@/lib/agents";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";
import {
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_TERMINEE,
  type AnalyticsCouts,
  type DetailExecution,
  type PageJournal,
} from "@/lib/types";

import PageTableauDeBord from "@/app/page";
import PageBrief from "@/app/brief/page";
import PageChat from "@/app/chat/page";
import PageComposer from "@/app/composer/page";
import PageCouts from "@/app/couts/page";
import PageJournalEcran from "@/app/journal/page";
import PageParametres from "@/app/parametres/page";
import PageRuns from "@/app/runs/page";
import PageValidations from "@/app/validations/page";

import { auditerLaPage, bloquantes, raconter } from "./axe";
import {
  agentFactice,
  coutExecutionFactice,
  coutTacheAgregeeFactice,
  entreeJournalFactice,
  evenementFactice,
  ficheCatalogueFactice,
  pageExplorateurFactice,
  pageJournalCourante,
  poserChemin,
  poserEtatGlobal,
  poserJournal,
  poserProjetActif,
  projetsDeclares,
  runFactice,
  tacheFactice,
  usageFactice,
  validationFactice,
} from "./aides";

const racine = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");

const lireSource = (relatif: string) =>
  readFileSync(path.join(racine, relatif), "utf8");

/**
 * Tout ce que le produit rend : les écrans (`app/`) et les composants. La liste
 * est **parcourue** et non écrite — un fichier neuf entre dans le périmètre du
 * jour où il est créé, ce qui est la seule façon qu'un balayage reste vrai.
 */
function sourcesDuProduit(): string[] {
  return ["app", "components"].flatMap((dossier) =>
    readdirSync(path.join(racine, dossier), { recursive: true })
      .map(String)
      .filter((f) => f.endsWith(".tsx"))
      .map((f) => path.posix.join(dossier, f.split(path.sep).join("/"))),
  );
}

// --- Le réseau, pour de bon ------------------------------------------------

const CATALOGUE = [
  ficheCatalogueFactice({ nom: "dev", role: "Développeur" }),
  ficheCatalogueFactice({ nom: "qa", role: "Testeur" }),
];

/** Le détail que l'écran « Valider le brief » ouvre sur le run en attente. */
function detailFactice(): DetailExecution {
  return {
    ...runFactice({ run_id: "run-1", statut: EXECUTION_EN_ATTENTE_BRIEF }),
    brief: {
      objectif: "Ajouter l'export CSV des dépenses",
      perimetre: ["Écran Dépenses", "API /export"],
      hors_perimetre: ["Le format XLSX"],
      contraintes: ["Pas de dépendance nouvelle"],
      criteres_acceptation: ["Le fichier s'ouvre dans un tableur"],
      hypotheses: ["Les montants sont déjà en euros"],
      questions: [],
    },
    cout: coutExecutionFactice(),
    evenements: [],
  };
}

vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    // Reconduits : ce mock **remplace** celui de `tests/setup.ts`.
    chargerProjets: async () => projetsDeclares(),
    chargerJournal: async (): Promise<PageJournal> => pageJournalCourante(),
    // Ce que le setup ne couvre pas, et sans quoi quatre des dix écrans
    // s'auditeraient à l'état « bannière d'erreur ».
    chargerCatalogue: async () => CATALOGUE,
    chargerSante: async () => ({ statut: "ok" }),
    chargerRegistreMcp: async () => [],
    chargerPoolMcp: async () => ({ integrations: [], erreur: null }),
    chargerExplorateur: async () => pageExplorateurFactice(),
    chargerDisponibiliteSelecteur: async () => ({
      disponible: false,
      motif: "hors_poste",
      message: "Le sélecteur natif n'est pas disponible ici.",
      outil: null,
    }),
    chargerExecution: async (): Promise<DetailExecution> => detailFactice(),
  };
});

/**
 * La vue analytics est mockée **au hook** et non à l'API, contrairement au
 * reste : `useAnalyticsCouts` ouvre sa propre WebSocket et se reconnecte en
 * backoff. La couper à la source laisserait le socle du principe « aucun test
 * n'a besoin de backend » (docs/10 §8) tenu par un `fetch` qui échoue et des
 * minuteurs qui survivent au test. Mock **partiel** : `PERIODES`, que la page
 * lit à côté du hook, passe tel quel.
 */
vi.mock("@/lib/useAnalyticsCouts", async (original) => ({
  ...(await original<Record<string, unknown>>()),
  useAnalyticsCouts: () => ({
    vue: vueAnalytics(),
    connecte: true,
    chargement: false,
    rafraichissement: false,
    erreur: null,
  }),
}));

function vueAnalytics(): AnalyticsCouts {
  const usage = usageFactice({
    appels: 12,
    tokens_entree: 4200,
    tokens_sortie: 900,
    tokens_total: 5100,
    cout_usd: 1.42,
    duree_ms: 43_000,
    tours: 3,
  });
  return {
    depuis: null,
    pas: "heure",
    projet: "prj-7f3a1c2b",
    portee: "prj-7f3a1c2b",
    total: usage,
    executions: [
      {
        run_id: "run-1",
        nb_taches: 3,
        debut: "2026-07-28T10:00:00Z",
        fin: "2026-07-28T10:12:00Z",
        usage,
        projet_id: "prj-7f3a1c2b",
      },
    ],
    agents: [{ agent: "dev", role: "Développeur", taches: 2, usage }],
    taches: [coutTacheAgregeeFactice({ usage })],
    serie: [{ periode: "2026-07-28T10:00:00Z", usage }],
  };
}

// --- L'état partagé, peuplé -------------------------------------------------

/**
 * Un projet qui travaille : des tâches à plusieurs statuts, deux agents, un
 * arbitrage en attente, deux runs — dont un arrêté sur son brief — et un grand
 * livre. Un écran vide n'a presque pas de balises : l'auditer rendrait un vert
 * qui ne parle que du vide, et c'est exactement le verdict qu'on ne veut pas.
 */
function peuplerEtat(): void {
  poserEtatGlobal({
    taches: [
      tacheFactice({ id: "T-1", statut: "en_cours", titre: "Écrire les tests" }),
      tacheFactice({
        id: "T-2",
        statut: "terminee",
        titre: "Poser le schéma",
        agent: "qa",
        cout_usd: 0.42,
      }),
      tacheFactice({ id: "T-3", statut: "backlog", titre: "Documenter" }),
    ],
    agents: [
      agentFactice({ nom: "dev", statut: "occupe", tache_courante: "T-1" }),
      agentFactice({ nom: "qa", role: "Testeur", taches_terminees: 4 }),
    ],
    evenements: [
      evenementFactice({ statut: "en_cours" }),
      evenementFactice({ tache_id: "T-2", statut: "terminee" }),
    ],
    validations: [validationFactice()],
    executions: [
      runFactice({ run_id: "run-1", statut: EXECUTION_EN_ATTENTE_BRIEF }),
      runFactice({
        run_id: "run-0",
        statut: EXECUTION_TERMINEE,
        nb_taches: 3,
        cout_usd: 1.42,
        fin: "2026-07-28T10:12:00Z",
      }),
    ],
    couts: [
      coutExecutionFactice({ total: usageFactice({ cout_usd: 1.42 }) }),
    ],
  });
  poserJournal([
    entreeJournalFactice({ titre: "Écrire les tests", statut: "en_cours" }),
    entreeJournalFactice({ id: "j-0002", titre: "Poser le schéma", statut: "terminee" }),
  ]);
}

// --- Les dix écrans ---------------------------------------------------------

/**
 * Un écran = une entrée de menu et le composant que sa route rend.
 *
 * `/agents` est le seul à ne pas passer par son fichier `page.tsx` : c'est un
 * composant **serveur `async`** qui ne fait que lire `?onglet=` avant de rendre
 * `ListeAgents`, et un composant async ne se monte pas dans Testing Library. On
 * rend donc ce qu'il rend, avec l'onglet qu'il aurait résolu — la coquille
 * sautée ne porte pas une balise.
 */
const ECRANS: { href: string; rendu: () => React.ReactElement }[] = [
  { href: "/", rendu: () => <PageTableauDeBord /> },
  { href: "/composer", rendu: () => <PageComposer /> },
  { href: "/brief", rendu: () => <PageBrief /> },
  { href: "/runs", rendu: () => <PageRuns /> },
  {
    href: "/agents",
    rendu: () => <ListeAgents ongletCible={ongletAgentOuDefaut(undefined)} />,
  },
  { href: "/chat", rendu: () => <PageChat /> },
  { href: "/couts", rendu: () => <PageCouts /> },
  { href: "/validations", rendu: () => <PageValidations /> },
  { href: "/journal", rendu: () => <PageJournalEcran /> },
  { href: "/parametres", rendu: () => <PageParametres /> },
];

/** Monte un écran dans son shell réel et attend que la garde du projet ouvre. */
async function monterEcran(ecran: (typeof ECRANS)[number]) {
  poserChemin(ecran.href);
  render(<Shell>{ecran.rendu()}</Shell>);
  // La barre supérieure titre la page depuis le menu : sa présence dit que la
  // garde du projet a tranché et que l'écran est monté sous le cadre.
  await screen.findByRole("heading", { level: 1 });
}

// --- 1. La sonde, prouvée avant de servir -----------------------------------

describe("la sonde d'accessibilité (tests/axe.ts)", () => {
  it("trouve les fautes qu'elle est censée trouver", async () => {
    // Trois fautes de trois familles différentes : une image sans alternative
    // (`critical`), un bouton sans nom accessible (`critical`), un champ sans
    // étiquette (`serious`). Si l'audit était mal branché — mauvais contexte,
    // règles éteintes — il rendrait « aucune violation » sur ce fragment-ci
    // exactement comme sur un écran sain. La troisième n'est pas décorative :
    // c'est la seule `serious` du lot, donc la seule qui prouve que le seuil du
    // ticket descend bien sous `critical`.
    //
    // ⚠ Les deux exemptions ci-dessous sont **la preuve que l'autre moitié du
    // lot fonctionne** : depuis que `jsx-a11y/recommended` est en `error`, ce
    // fragment ne compile plus au lint — c'est exactement ce qu'on lui demande
    // partout ailleurs. Il faut donc le dire ici, à la ligne près et pour ces
    // règles-là ; un `eslint-disable` de fichier éteindrait aussi le fragment
    // sain d'à côté, qui doit rester jugé.
    render(
      <div>
        {/* eslint-disable-next-line jsx-a11y/alt-text, @next/next/no-img-element */}
        <img src="/x.png" />
        <button type="button" />
        <input type="text" />
      </div>,
    );
    const trouvees = bloquantes(await auditerLaPage());
    expect(trouvees.map((v) => v.id).sort()).toEqual(
      expect.arrayContaining(["button-name", "image-alt", "label"]),
    );
  });

  it("ne rend rien sur un fragment sain", async () => {
    // Le pendant du contrôle ci-dessus : une sonde qui crierait sur tout ne
    // dirait pas davantage qu'une sonde muette.
    render(
      <main>
        <h1>Titre</h1>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/x.png" alt="Un graphique" />
        <button type="button">Agir</button>
      </main>,
    );
    const violations = await auditerLaPage();
    expect(bloquantes(violations), raconter(violations)).toHaveLength(0);
  });

  it("garde ce que le harnais ne peut pas juger : langue et titre du document", () => {
    // `html-has-lang` et `document-title` sont écartées de l'audit : le `<html>`
    // vient de `app/layout.tsx` et le titre de son `metadata`, deux choses que
    // le rendu d'un composant ne monte pas — jsdom sert le sien, nu. Les règles
    // ne disparaissent pas pour autant, elles changent de juge : c'est la source
    // du layout qui répond, comme `globals.css` répond du contraste (#534).
    const layout = lireSource("app/layout.tsx");
    expect(layout).toContain('lang="fr"');
    expect(layout).toMatch(/title:\s*"[^"]+"/);
  });
});

// --- 2. Les dix écrans ------------------------------------------------------

describe("les dix écrans face à axe-core", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("audite exactement les écrans du menu", () => {
    // La table ci-dessus est **dérivée**, pas recopiée : une page ajoutée au
    // menu sans cas d'audit fait rougir ici, au lieu d'échapper au filet en
    // silence. C'est le même contrôle que celui qui confronte les entrées de
    // menu aux routes réelles (`navigation.test.tsx`).
    expect(ECRANS.map((e) => e.href)).toEqual(MENU.map((e) => e.href));
  });

  for (const ecran of ECRANS) {
    it(`ne laisse aucune violation serious/critical sur ${ecran.href}`, async () => {
      await monterEcran(ecran);
      const violations = await auditerLaPage();
      expect(bloquantes(violations), `\n${raconter(violations)}\n`).toHaveLength(0);
    });
  }
});

// --- 3. Ce qu'axe ne voit pas ----------------------------------------------

describe("le lien d'évitement (WCAG 2.2 §2.4.1)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("est le premier arrêt de la tabulation, et vise le contenu", async () => {
    await monterEcran(ECRANS[0]);
    const lien = screen.getByRole("link", { name: "Aller au contenu principal" });
    expect(lien).toHaveAttribute("href", `#${ID_CONTENU_PRINCIPAL}`);

    // « Premier » se vérifie dans l'ordre du DOM et non à l'écran : c'est lui
    // qui décide de l'ordre de tabulation, et un lien d'évitement qui arrive
    // après la navigation ne sert à rien — c'est précisément la navigation
    // qu'il fait sauter.
    const focalisables = document.querySelectorAll<HTMLElement>(
      "a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])",
    );
    expect(focalisables[0]).toBe(lien);
  });

  it("mène à un `<main>` que le focus peut atteindre", async () => {
    await monterEcran(ECRANS[0]);
    const contenu = document.getElementById(ID_CONTENU_PRINCIPAL);
    expect(contenu?.tagName).toBe("MAIN");
    // Sans `tabindex="-1"`, suivre l'ancre déplace le point d'insertion du
    // document mais pas le focus : la tabulation suivante repartirait du menu.
    expect(contenu).toHaveAttribute("tabindex", "-1");
    // Et il doit être **atteignable au clavier sans être une étape** : un
    // `tabindex="0"` ajouterait un arrêt de tabulation sur une zone entière.
    contenu?.focus();
    expect(document.activeElement).toBe(contenu);
  });
});

describe("la garde de mouvement (WCAG 2.2 §2.3.3)", () => {
  /**
   * Toute utilité Tailwind d'animation trouvée dans une chaîne de classes, avec
   * le fait de savoir si elle est gardée.
   *
   * Le balayage se fait **sur les chaînes littérales, commentaires retirés** —
   * pas sur les lignes brutes. Sans les deux filtres, la prose du dépôt ferait
   * le gros du résultat : ce fichier-ci, comme `GuidePriseEnMain`, parle de
   * « transition » en français et cite `transition-none` entre accents graves.
   */
  function chainesDeClasses(source: string): string[][] {
    const sansCommentaires = source
      .replace(/\/\*[\s\S]*?\*\//g, " ")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    return [
      ...sansCommentaires.matchAll(/"([^"\n]*)"|'([^'\n]*)'|`([^`]*)`/g),
    ].map(([, guillemets, apostrophes, gabarit]) =>
      (guillemets ?? apostrophes ?? gabarit ?? "")
        .split(/[\s${}]+/)
        // Un gabarit avale les chaînes qu'il interpole : les guillemets de
        // l'expression ternaire restent collés au jeton (`"animate-pulse`).
        .map((jeton) => jeton.replace(/^["'`]+|["'`]+$/g, "")),
    );
  }

  /** Ce qui bouge dans une chaîne de classes, gardé ou non. */
  function mouvementsDe(jetons: string[]): { nus: string[]; gardes: number } {
    const nus = jetons.filter(
      (j) =>
        /^(transition(-\[[^\]]*\]|-[a-z]+)?|animate-[a-z-]+)$/.test(j) &&
        j !== "transition-none" &&
        j !== "animate-none",
    );
    const gardes = jetons.filter((j) =>
      /^motion-reduce:(transition|animate)-none$/.test(j),
    ).length;
    return { nus, gardes };
  }

  it("reconnaît une transition nue, et ne crie pas sur une transition gardée", () => {
    // La sonde avant ce qu'elle mesure, comme plus haut : un balayage dont le
    // motif ne matcherait plus rien rendrait « 0 transition nue » avec les mots
    // de « tout est gardé ».
    const lire = (source: string) => chainesDeClasses(source).map(mouvementsDe);
    expect(lire('"gap-2 transition-colors"')).toEqual([
      { nus: ["transition-colors"], gardes: 0 },
    ]);
    expect(lire('"animate-pulse motion-reduce:animate-none"')).toEqual([
      { nus: ["animate-pulse"], gardes: 1 },
    ]);
    // La forme du produit : une garde interpolée dans un gabarit.
    expect(lire('`x ${a ? "transition motion-reduce:transition-none" : ""}`')).toEqual(
      [{ nus: ["transition"], gardes: 1 }],
    );
    // Et ce qu'il doit ignorer : la prose, et l'utilité qui *est* la garde.
    expect(lire("/* une `transition` douce */")).toEqual([]);
    expect(lire('"transition-none"')).toEqual([{ nus: [], gardes: 0 }]);
  });

  it("garde chaque transition et chaque animation du produit", () => {
    // Le contrôle est **par chaîne de classes** et non par fichier : deux
    // gardes sur une chaîne ne rachètent pas une transition nue sur la chaîne
    // d'à côté, et c'est bien ce qui se passerait dans un fichier qui en porte
    // trois (`VuePipeline`).
    const fautifs: string[] = [];
    let mouvements = 0;
    for (const fichier of sourcesDuProduit()) {
      for (const jetons of chainesDeClasses(lireSource(fichier))) {
        const { nus, gardes } = mouvementsDe(jetons);
        mouvements += nus.length;
        if (nus.length > gardes) {
          fautifs.push(`  ${fichier} — sans garde : ${nus.join(", ")}`);
        }
      }
    }
    expect(fautifs, `\n${fautifs.join("\n")}\n`).toHaveLength(0);
    // Le plancher rend le ✓ opposable : un motif devenu muet — utilité Tailwind
    // renommée, chaînes construites autrement — rendrait « aucune transition
    // nue » avec les mots de « tout est gardé ». 19 est le compte **mesuré** au
    // lot (15 transitions, 4 animations) ; docs/30 §3.4 en relevait 19 et 4
    // avant que les écrans de #472 ne bougent. Un lot qui en retire
    // légitimement une baisse ce chiffre — comme #534 fait de ses 36 paires.
    expect(mouvements).toBeGreaterThanOrEqual(19);
  });
});

describe("la taille des cibles (WCAG 2.2 §2.5.8)", () => {
  /**
   * ⚠ Ce que ce contrôle **ne fait pas** : mesurer. jsdom ne calcule ni
   * hauteur, ni interligne, ni marge — c'est la frontière posée par #308, et le
   * pixel appartient au skill `/banc-mise-en-page`. Ce qui se garde ici est la
   * **déclaration** : une cible dont la feuille de classes ne promet pas 24 px.
   *
   * Il ne juge donc que les cibles qui **portent leur propre pas
   * typographique** (`text-annexe`, `text-micro`, `text-xs`) : sans lui, la
   * hauteur dépend d'un interligne hérité que rien ici ne connaît, et flaguer
   * au hasard ferait d'un filet une nuisance qu'on finit par éteindre. Ce n'est
   * pas un périmètre arbitraire : c'est **exactement** la famille où le défaut a
   * été mesuré — « quelques liens de renvoi à 22 px » (docs/30 §3.4), tous
   * écrits en petit corps sans plancher.
   *
   * Les variantes tombent avant l'examen (`focus:py-2` compte comme `py-2`) :
   * le lien d'évitement n'existe qu'au focus, et le juger sur son état caché
   * reviendrait à ne pas le juger. Le pas Tailwind vaut `0.25rem` — `6` = 24 px
   * pour une hauteur, `1.5` = 12 px de marge, qui s'ajoutent deux fois à une
   * ligne d'au moins 16 px.
   */
  const PAS_MENUS = ["text-annexe", "text-micro", "text-xs"];

  function utilites(classes: string): string[] {
    return classes
      .split(/\s+/)
      .map((jeton) => jeton.slice(jeton.lastIndexOf(":") + 1));
  }

  /** La cible écrit-elle son propre corps de texte — donc sa propre hauteur ? */
  function porteSonPas(classes: string): boolean {
    return utilites(classes).some((u) => PAS_MENUS.includes(u));
  }

  function declareUnPlancher(classes: string): boolean {
    return utilites(classes).some((u) => {
      const hauteur = /^(?:min-h|h|size)-(\d+(?:\.\d+)?)$/.exec(u);
      if (hauteur) return Number(hauteur[1]) >= 6;
      const marge = /^(?:p|py)-(\d+(?:\.\d+)?)$/.exec(u);
      return marge !== null && Number(marge[1]) >= 1.5;
    });
  }

  it("reconnaît un plancher, et refuse ce qui n'en a pas", () => {
    expect(declareUnPlancher("inline-flex text-annexe")).toBe(false);
    expect(declareUnPlancher("inline-flex min-h-6 text-annexe")).toBe(true);
    expect(declareUnPlancher("rounded px-3 py-1.5")).toBe(true);
    expect(declareUnPlancher("rounded px-3 py-0.5")).toBe(false);
    expect(declareUnPlancher("sr-only focus:not-sr-only focus:py-2")).toBe(true);
    expect(declareUnPlancher("size-12 rounded-full")).toBe(true);
  });

  it("ne se prononce que sur les cibles qui portent leur pas", () => {
    // Le pendant du contrôle ci-dessus : sans cette borne, le sélecteur de
    // projet (`px-2 py-1 text-sm`, 28 px en vrai) serait rendu fautif par un
    // `py-1` lu hors de son interligne — un faux positif par écran.
    expect(porteSonPas("inline-flex text-annexe font-medium")).toBe(true);
    expect(porteSonPas("rounded-md px-2 py-1 text-sm")).toBe(false);
  });

  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  for (const ecran of ECRANS) {
    it(`ne laisse aucune cible sous 24 px sur ${ecran.href}`, async () => {
      await monterEcran(ecran);
      const maigres = [...document.querySelectorAll<HTMLElement>("a[href], button")]
        .map((cible) => ({ cible, classes: cible.getAttribute("class") ?? "" }))
        .filter(
          ({ classes }) => porteSonPas(classes) && !declareUnPlancher(classes),
        )
        .map(
          ({ cible, classes }) =>
            `  <${cible.tagName.toLowerCase()}> « ${(cible.textContent ?? "").trim().slice(0, 40)} » — ${classes}`,
        );
      expect(maigres, `\n${maigres.join("\n")}\n`).toHaveLength(0);
    });
  }
});

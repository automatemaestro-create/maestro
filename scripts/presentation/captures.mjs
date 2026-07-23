/**
 * Captures d'écran de la Control Tower pour /milestone-presentation (#142).
 *
 * Pilote un vrai navigateur (playwright-core + l'Edge déjà installé sur la
 * machine — pas de navigateur Playwright téléchargé, cf. skill `verify`) et
 * photographie les pages du menu principal, puis écrit un manifeste JSON que
 * `build.py` consomme.
 *
 * Ce script ne démarre rien : il suppose la Control Tower déjà en ligne. C'est
 * `captures.sh` qui enchaîne démarrage, bootstrap de playwright-core et appel.
 *
 *   node scripts/presentation/captures.mjs --sortie <dossier> [--base http://127.0.0.1:3000]
 *
 * Une page qui échoue (route cassée, timeout) n'interrompt pas la série : elle
 * est consignée dans le manifeste avec son erreur et les autres continuent —
 * une présentation partiellement illustrée vaut mieux que pas de présentation.
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const RACINE = resolve(dirname(fileURLToPath(import.meta.url)), "../..");

/** Le menu dont on part si `navigation.ts` devient illisible (voir lireMenu). */
const MENU_REPLI = [
  { href: "/", libelle: "Tableau de bord" },
  { href: "/catalogue", libelle: "Agents" },
  { href: "/playbooks", libelle: "Playbooks" },
  { href: "/chat", libelle: "Chat" },
  { href: "/couts", libelle: "Coûts & analytics" },
  { href: "/validations", libelle: "Validations" },
  { href: "/parametres", libelle: "Paramètres" },
];

/** Largeur de capture : un écran de travail, pas un mobile — c'est un backoffice. */
const VIEWPORT = { width: 1440, height: 900 };

/** Délai laissé au scénario de démo pour peupler l'écran une fois les données là (ms). */
const REPOS_MS = 1500;

/** Attente max du signal « page prête » (WebSocket ouverte, plus de placeholder). */
const PRET_MS = 20_000;

/**
 * Marqueurs de l'UI qui disent qu'une page est vraiment prête à être photographiée :
 * la barre supérieure affiche « Temps réel connecté » (WebSocket ouverte) et plus aucun
 * placeholder « Chargement… » ne subsiste. Sans cette attente, les captures montrent l'état
 * transitoire « Reconnexion… / Chargement de l'état… » — c'est-à-dire rien.
 */
const MARQUEUR_CONNECTE = "Temps réel connecté";
const MARQUEUR_CHARGEMENT = "Chargement";

function arguments_() {
  const brut = process.argv.slice(2);
  const args = { sortie: null, base: "http://127.0.0.1:3000" };
  for (let i = 0; i < brut.length; i += 1) {
    if (brut[i] === "--sortie") args.sortie = brut[++i];
    else if (brut[i] === "--base") args.base = brut[++i];
    else throw new Error(`argument inconnu : ${brut[i]}`);
  }
  if (!args.sortie) throw new Error("--sortie <dossier> est requis");
  return args;
}

/**
 * Le menu principal, lu dans `apps/web/lib/navigation.ts` — la source unique de
 * l'UI (#117). On l'extrait au lieu de le recopier : une page ajoutée au menu
 * entre donc d'elle-même dans la présentation, sans toucher à ce script.
 */
async function lireMenu() {
  const chemin = join(RACINE, "apps/web/lib/navigation.ts");
  try {
    const source = await readFile(chemin, "utf8");
    const entrees = [
      ...source.matchAll(/\{\s*href:\s*"([^"]+)"\s*,\s*libelle:\s*"([^"]+)"/g),
    ].map(([, href, libelle]) => ({ href, libelle }));
    if (entrees.length > 0) return entrees;
    console.error(`[captures] menu illisible dans ${chemin} — repli sur la liste intégrée`);
  } catch (erreur) {
    console.error(`[captures] ${chemin} introuvable (${erreur.message}) — repli sur la liste intégrée`);
  }
  return MENU_REPLI;
}

/** `/` → `accueil` ; `/couts` → `couts` — le nom de fichier et la clé du manifeste. */
function cleDeRoute(href) {
  const nu = href.replace(/^\/+|\/+$/g, "").replace(/[^a-zA-Z0-9-]+/g, "-");
  return nu === "" ? "accueil" : nu;
}

/**
 * playwright-core n'est pas une dépendance du dépôt (il n'a rien à faire dans
 * le build de l'UI) : `captures.sh` l'installe dans un dossier temporaire et
 * passe son chemin par MAESTRO_PLAYWRIGHT_HOME. On tente d'abord la résolution
 * normale, pour rester utilisable depuis un environnement qui l'a déjà.
 */
async function chargerPlaywright() {
  let module_;
  try {
    module_ = await import("playwright-core");
  } catch {
    const maison = process.env.MAESTRO_PLAYWRIGHT_HOME;
    if (!maison) {
      throw new Error(
        "playwright-core introuvable. Passer par scripts/presentation/captures.sh, " +
          "qui l'installe dans un dossier temporaire et pose MAESTRO_PLAYWRIGHT_HOME.",
      );
    }
    const require_ = createRequire(join(maison, "package.json"));
    module_ = await import(pathToFileURL(require_.resolve("playwright-core")).href);
  }
  // playwright-core est en CommonJS : selon la façon dont il est résolu, l'import ESM expose
  // ses entrées soit à plat, soit sous `default`. On accepte les deux plutôt que de parier.
  const playwright = module_.chromium ? module_ : module_.default;
  if (!playwright?.chromium) {
    throw new Error("playwright-core chargé mais sans `chromium` — installation incomplète ?");
  }
  return playwright;
}

async function principal() {
  const args = arguments_();
  const sortie = resolve(args.sortie);
  await mkdir(sortie, { recursive: true });

  const menu = await lireMenu();
  const { chromium } = await chargerPlaywright();

  const navigateur = await chromium.launch({ channel: "msedge", headless: true });
  const contexte = await navigateur.newContext({
    viewport: VIEWPORT,
    // Thème clair imposé des deux façons : par la préférence système (l'UI
    // d'avant #118 la suit) et par le choix persisté (l'UI d'après la lit).
    // Sans ça, la série de captures dépendrait du thème de la machine.
    colorScheme: "light",
  });
  await contexte.addInitScript(() => {
    try {
      window.localStorage.setItem("maestro.theme", "clair");
    } catch {
      // Stockage indisponible : la préférence système suffit.
    }
  });

  const pages = [];
  const page = await contexte.newPage();

  for (const { href, libelle } of menu) {
    const cle = cleDeRoute(href);
    const fichier = `${cle}.png`;
    const url = new URL(href, args.base).href;
    let complet = true;
    try {
      await page.goto(url, { waitUntil: "networkidle", timeout: 30_000 });
      try {
        await page.waitForFunction(
          ([connecte, chargement]) => {
            const texte = document.body.innerText;
            return texte.includes(connecte) && !texte.includes(chargement);
          },
          [MARQUEUR_CONNECTE, MARQUEUR_CHARGEMENT],
          { timeout: PRET_MS },
        );
      } catch {
        // On photographie quand même : une page à moitié peuplée reste plus parlante qu'un trou
        // dans la présentation. `complet: false` laisse l'appelant décider de l'écarter.
        complet = false;
        console.error(`[captures] ⚠ ${libelle} : données incomplètes, capture quand même`);
      }
      await page.waitForTimeout(REPOS_MS);
      await page.screenshot({ path: join(sortie, fichier), type: "png" });
      console.error(`[captures] ${libelle} → ${fichier}`);
      pages.push({ cle, href, libelle, fichier, complet, erreur: null });
    } catch (erreur) {
      console.error(`[captures] ⚠ ${libelle} (${url}) : ${erreur.message}`);
      pages.push({ cle, href, libelle, fichier: null, complet: false, erreur: erreur.message });
    }
  }

  await navigateur.close();

  const manifeste = {
    base: args.base,
    genere: new Date().toISOString(),
    viewport: VIEWPORT,
    pages,
  };
  await writeFile(join(sortie, "captures.json"), `${JSON.stringify(manifeste, null, 2)}\n`, "utf8");

  const reussies = pages.filter((p) => p.fichier).length;
  console.error(`[captures] ${reussies}/${pages.length} page(s) capturée(s) dans ${sortie}`);
  // Zéro capture = échec : l'appelant doit pouvoir enchaîner sur le repli
  // « présentation sans visuels » plutôt que de croire la série réussie.
  if (reussies === 0) process.exitCode = 1;
}

principal().catch((erreur) => {
  console.error(`[captures] échec : ${erreur.message}`);
  process.exitCode = 1;
});

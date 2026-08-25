/**
 * Captures d'écran et démonstrations filmées de la Control Tower, pour
 * /milestone-presentation (#142, puis #545).
 *
 * Pilote un vrai navigateur (playwright-core + l'Edge déjà installé sur la
 * machine — pas de navigateur Playwright téléchargé, cf. skill `verify`) et
 * produit deux séries, dans cet ordre :
 *
 *   1. les **captures** des pages du menu principal ;
 *   2. les **clips** des parcours de démonstration déclarés dans `parcours.mjs`.
 *
 * Puis écrit un manifeste JSON que `build.py` consomme (`pages` et `videos`).
 *
 * L'ordre n'est pas indifférent : les captures d'abord, parce qu'elles sont le
 * livrable historique et qu'un tournage qui part mal ne doit pas les emporter ;
 * et parce que la série de captures a déjà visité chaque route, donc réchauffé
 * le serveur — un clip ne perd plus sa première seconde à attendre un rendu.
 *
 * Ce script ne démarre rien : il suppose la Control Tower déjà en ligne. C'est
 * `captures.sh` qui enchaîne démarrage, bootstrap de playwright-core et appel.
 *
 *   node scripts/presentation/captures.mjs --sortie <dossier> \
 *        [--base http://127.0.0.1:3000] [--sans-videos]
 *
 * Une page qui échoue (route cassée, timeout) n'interrompt pas la série : elle
 * est consignée dans le manifeste avec son erreur et les autres continuent —
 * une présentation partiellement illustrée vaut mieux que pas de présentation.
 * **Même règle pour un parcours** : il laisse sa ligne avec son erreur, les
 * autres parcours continuent, et les captures ne s'en aperçoivent pas. Le code
 * de retour ne dépend donc que des captures.
 */

import { mkdir, readFile, rm, stat, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { DEFILEMENT_MS_DEFAUT, DUREE_MAX_MS_DEFAUT, PARCOURS } from "./parcours.mjs";

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

/**
 * Clés `localStorage` de l'UI — `apps/web/lib/theme.ts`, `lib/projetActif.ts`,
 * `lib/guide.ts`. Toutes les trois disent la même chose : un contexte de
 * navigateur neuf n'a **aucune mémoire**, et la série doit lui en donner une,
 * sans quoi elle rendrait ce que l'application montre à un inconnu plutôt que ce
 * qu'elle montre à quelqu'un qui s'en sert.
 */
const CLE_THEME = "maestro.theme";
const CLE_PROJET_ACTIF = "maestro.projet.actif";
const CLE_GUIDE_VU = "maestro.guide.vu";

/**
 * Le projet dans lequel s'ouvre la Control Tower de démo.
 *
 * Sans lui, **toute la série montre la porte d'entrée** et rien d'autre : le
 * shell n'affiche le tableau de bord qu'une fois un projet actif (#279), et
 * l'identifiant retenu vit dans le `localStorage` du navigateur. C'est donc ici
 * qu'il se pose, à côté du thème et pour la même raison — sinon la série
 * dépendrait de ce que la machine a mémorisé.
 *
 * L'identifiant vient de `captures.sh`, qui le lit dans `demo.py` (`PROJET_ID`)
 * et déclare le projet correspondant côté API ; le repli couvre l'appel direct
 * de ce script contre une stack déjà montée.
 */
const PROJET_DEMO = process.env.MAESTRO_PROJET_DEMO || "prj-demo";

/**
 * Ce qu'un geste `cliquer` accepte de viser quand il est déclaré par son texte.
 * La liste est délibérément courte : un parcours qui doit cliquer autre chose
 * passe par `selecteur`, plutôt que d'élargir la cible pour tout le monde.
 */
const CIBLES_CLIQUABLES = [
  "a",
  "button",
  "summary",
  "label",
  '[role="button"]',
  '[role="tab"]',
  '[role="link"]',
  '[role="menuitem"]',
  '[role="menuitemradio"]',
  '[role="checkbox"]',
  'input[type="checkbox"]',
].join(", ");

/** Patience maximale d'un geste, en plus du plafond du clip (ms). */
const PATIENCE_GESTE_MS = 8_000;

function arguments_() {
  const brut = process.argv.slice(2);
  const args = { sortie: null, base: "http://127.0.0.1:3000", videos: true };
  for (let i = 0; i < brut.length; i += 1) {
    if (brut[i] === "--sortie") args.sortie = brut[++i];
    else if (brut[i] === "--base") args.base = brut[++i];
    else if (brut[i] === "--sans-videos") args.videos = false;
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

/**
 * Un contexte de navigateur tel que la présentation les veut tous : même
 * fenêtre, même thème, même projet actif. `extra` ajoute ce qui distingue un
 * usage de l'autre — l'enregistrement vidéo, et rien d'autre à ce jour.
 *
 * Un contexte par usage plutôt qu'un contexte partagé : Playwright écrit **une
 * vidéo par page** et ne la finalise qu'à la fermeture de la page ou du
 * contexte, si bien que filmer et photographier dans le même contexte rendrait
 * un seul clip pour toute la série.
 */
async function nouveauContexte(navigateur, extra = {}) {
  const contexte = await navigateur.newContext({
    viewport: VIEWPORT,
    // Thème clair imposé des deux façons : par la préférence système (l'UI
    // d'avant #118 la suit) et par le choix persisté (l'UI d'après la lit).
    // Sans ça, la série de captures dépendrait du thème de la machine.
    colorScheme: "light",
    ...extra,
  });
  await contexte.addInitScript(
    ([cleTheme, cleProjet, cleGuide, projet]) => {
      try {
        window.localStorage.setItem(cleTheme, "clair");
        window.localStorage.setItem(cleProjet, projet);
        // La visite guidée s'ouvre au premier passage (#116) : sa fenêtre et son
        // voile `fixed inset-0` recouvrent l'application — ils masquent les
        // captures et **interceptent les clics** des parcours, qui échouaient
        // tous sur leur premier geste. On la marque vue.
        window.localStorage.setItem(cleGuide, "1");
      } catch {
        // Stockage indisponible : la préférence système suffit pour le thème,
        // et la porte d'entrée dira elle-même qu'aucun projet n'est ouvert.
      }
    },
    [CLE_THEME, CLE_PROJET_ACTIF, CLE_GUIDE_VU, PROJET_DEMO],
  );
  return contexte;
}

/**
 * Attend le signal « page prête » — WebSocket ouverte, plus de placeholder.
 * Lève si le signal ne vient pas : c'est à l'appelant de décider s'il
 * photographie/filme quand même.
 */
async function attendrePret(page, delai) {
  await page.waitForFunction(
    ([connecte, chargement]) => {
      const texte = document.body.innerText;
      return texte.includes(connecte) && !texte.includes(chargement);
    },
    [MARQUEUR_CONNECTE, MARQUEUR_CHARGEMENT],
    { timeout: delai },
  );
}

/** Ce qu'il reste du plafond d'un clip (ms) — négatif quand il est dépassé. */
function reste(echeance) {
  return echeance - Date.now();
}

/**
 * La patience accordée à une opération : jamais plus que ce qu'il reste du
 * plafond, jamais moins d'une demi-seconde — un `timeout: 0` vaut « sans
 * limite » chez Playwright, ce qui est exactement l'inverse de l'intention.
 */
function patience(echeance, plafond = PATIENCE_GESTE_MS) {
  return Math.max(500, Math.min(plafond, reste(echeance)));
}

/** L'élément que vise un geste `cliquer`, par son texte ou par son sélecteur. */
async function cibleCliquable(page, geste) {
  if (geste.selecteur) return page.locator(geste.selecteur).first();
  if (!geste.texte) throw new Error("`cliquer` sans `texte` ni `selecteur`");
  // Exact d'abord : « Tout » est un bouton de période, mais aussi un morceau de
  // « Tous les runs » et de « Totaux ». Le repli sur le contenu partiel sert les
  // cibles dont le texte visible ne se limite pas au libellé (une carte, par
  // exemple, porte son titre puis son sous-titre).
  const echappe = geste.texte.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const exact = page
    .locator(CIBLES_CLIQUABLES)
    .filter({ hasText: new RegExp(`^\\s*${echappe}\\s*$`) })
    .first();
  if ((await exact.count()) > 0) return exact;
  return page.locator(CIBLES_CLIQUABLES).filter({ hasText: geste.texte }).first();
}

/**
 * Défile la colonne de contenu, de façon **animée** : un saut de défilement ne
 * se voit pas sur un clip, une glissade oui.
 *
 * La cible n'est pas la fenêtre : le shell pose `overflow: hidden` sur le
 * `<body>` (#248) et confie le défilement à la colonne de contenu, si bien qu'un
 * `window.scrollTo` ne bougerait rien à l'écran. On prend donc l'élément qui a
 * le plus à faire défiler, et `document.scrollingElement` à défaut.
 */
async function defiler(page, vers, duree) {
  await page.evaluate(
    async ([cible_, duree_]) => {
      const defilants = [...document.querySelectorAll("*")].filter((element) => {
        const style = getComputedStyle(element);
        return (
          /(auto|scroll)/.test(style.overflowY) &&
          element.scrollHeight - element.clientHeight > 8
        );
      });
      defilants.sort(
        (a, b) =>
          b.scrollHeight - b.clientHeight - (a.scrollHeight - a.clientHeight),
      );
      const boite = defilants[0] ?? document.scrollingElement ?? document.body;
      const maximum = Math.max(0, boite.scrollHeight - boite.clientHeight);
      const depart = boite.scrollTop;
      const arrivee =
        cible_ === "bas"
          ? maximum
          : cible_ === "haut"
            ? 0
            : Math.max(0, Math.min(maximum, Number(cible_) || 0));
      if (arrivee === depart) return;
      const debut = performance.now();
      await new Promise((resoudre) => {
        const pas = (instant) => {
          const avancement = Math.min(1, (instant - debut) / Math.max(1, duree_));
          // Adouci aux deux bouts : un démarrage et un arrêt francs se lisent
          // comme un défaut de lecture, pas comme un geste.
          const adouci =
            avancement < 0.5
              ? 2 * avancement * avancement
              : 1 - (-2 * avancement + 2) ** 2 / 2;
          boite.scrollTop = depart + (arrivee - depart) * adouci;
          if (avancement < 1) requestAnimationFrame(pas);
          else resoudre();
        };
        requestAnimationFrame(pas);
      });
    },
    [vers, duree],
  );
}

/** Joue un geste ; lève si la cible n'est pas là ou si le verbe est inconnu. */
async function jouerGeste(page, geste, echeance) {
  switch (geste.type) {
    case "attendre":
      if (geste.texte) {
        await page
          .getByText(geste.texte)
          .first()
          .waitFor({ state: "visible", timeout: patience(echeance) });
      }
      break;
    case "cliquer": {
      const cible = await cibleCliquable(page, geste);
      await cible.click({ timeout: patience(echeance) });
      break;
    }
    case "defiler":
      await defiler(
        page,
        geste.vers ?? 0,
        Math.min(geste.ms ?? DEFILEMENT_MS_DEFAUT, patience(echeance)),
      );
      // Le `ms` d'un défilement EST sa durée : pas de temps de pause en plus.
      return;
    default:
      throw new Error(`geste inconnu : ${geste.type}`);
  }
  if (geste.ms) await page.waitForTimeout(Math.min(geste.ms, patience(echeance)));
}

/**
 * Joue la suite de gestes. Rend `null` si elle est allée au bout, sinon le motif
 * de l'arrêt — plafond atteint, ou geste en échec. Ne lève jamais : un parcours
 * écourté garde son clip, qui montre ce qu'il a eu le temps de montrer.
 */
async function jouerGestes(page, gestes, echeance) {
  for (const [rang, geste] of gestes.entries()) {
    const position = `geste ${rang + 1}/${gestes.length}`;
    if (reste(echeance) <= 0) return `durée plafonnée atteinte au ${position}`;
    try {
      await jouerGeste(page, geste, echeance);
    } catch (erreur) {
      return `${position} (${geste.type}) : ${erreur.message}`;
    }
  }
  return null;
}

/**
 * Filme un parcours et rend sa ligne de manifeste.
 *
 * Un clip = un contexte + une page qu'on ferme : Playwright ne finalise la
 * vidéo qu'à la fermeture, et `video.saveAs` n'a de chemin à donner qu'après.
 *
 * Le plafond couvre **tout le clip** — navigation et attente de la page
 * comprises. C'est ce que « durée plafonnée par clip » veut dire, et c'est aussi
 * ce qui empêche une page qui ne se peuple jamais de tenir la caméra vingt
 * secondes sur un écran vide.
 */
async function filmer(navigateur, base, sortie, dossierBrut, parcours) {
  const { cle, libelle, route } = parcours;
  const plafond = parcours.duree_max_ms ?? DUREE_MAX_MS_DEFAUT;
  const fichier = `${cle}.webm`;
  const url = new URL(route, base).href;
  const debut = Date.now();
  let contexte = null;
  try {
    contexte = await nouveauContexte(navigateur, {
      // Même taille que les captures : deux formats dans une même présentation
      // se remarquent immédiatement, et pour rien.
      recordVideo: { dir: dossierBrut, size: VIEWPORT },
    });
    const page = await contexte.newPage();
    const echeance = Date.now() + plafond;
    let motif = null;
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: patience(echeance) });
      await attendrePret(page, patience(echeance, PRET_MS));
    } catch (erreur) {
      // Le clip est conservé — même règle que la capture d'une page à moitié
      // peuplée : il montre au moins ce que la page a su rendre. Les gestes,
      // eux, sont abandonnés : ils s'ancrent sur des textes que cette page n'a
      // pas, et les jouer ne ferait que consommer le plafond en échouant.
      motif = `page non prête : ${erreur.message}`;
    }
    motif = motif ?? (await jouerGestes(page, parcours.gestes ?? [], echeance));

    const video = page.video();
    await page.close();
    await contexte.close();
    contexte = null;
    if (!video) throw new Error("aucun enregistrement ouvert pour cette page");
    await video.saveAs(join(sortie, fichier));
    await video.delete().catch(() => {});

    const { size } = await stat(join(sortie, fichier));
    const duree = Date.now() - debut;
    if (motif) console.error(`[parcours] ⚠ ${libelle} : ${motif} — clip écourté, conservé`);
    console.error(
      `[parcours] ${libelle} → ${fichier} (${(duree / 1000).toFixed(1)} s, ${Math.round(size / 1024)} Kio)`,
    );
    return {
      cle,
      libelle,
      fichier,
      duree_ms: duree,
      octets: size,
      complet: motif === null,
      erreur: motif,
    };
  } catch (erreur) {
    console.error(`[parcours] ⚠ ${libelle} (${url}) : ${erreur.message}`);
    if (contexte) await contexte.close().catch(() => {});
    return {
      cle,
      libelle,
      fichier: null,
      duree_ms: Date.now() - debut,
      octets: null,
      complet: false,
      erreur: erreur.message,
    };
  }
}

/**
 * Filme tous les parcours déclarés, l'un après l'autre. En série à dessein : le
 * scénario de démo est un état partagé, et deux parcours qui le manipulent en
 * même temps se filmeraient l'un l'autre.
 */
async function filmerParcours(navigateur, base, sortie) {
  // Playwright nomme les vidéos lui-même : elles atterrissent d'abord ici, puis
  // `video.saveAs` leur donne le nom du parcours dans le dossier de sortie.
  const dossierBrut = join(sortie, "videos-brutes");
  await mkdir(dossierBrut, { recursive: true });
  const videos = [];
  for (const parcours of PARCOURS) {
    videos.push(await filmer(navigateur, base, sortie, dossierBrut, parcours));
  }
  await rm(dossierBrut, { recursive: true, force: true }).catch(() => {});
  return videos;
}

async function principal() {
  const args = arguments_();
  const sortie = resolve(args.sortie);
  await mkdir(sortie, { recursive: true });

  const menu = await lireMenu();
  const { chromium } = await chargerPlaywright();

  const navigateur = await chromium.launch({ channel: "msedge", headless: true });
  const contexte = await nouveauContexte(navigateur);

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
        await attendrePret(page, PRET_MS);
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

  const reussies = pages.filter((p) => p.fichier).length;
  console.error(`[captures] ${reussies}/${pages.length} page(s) capturée(s) dans ${sortie}`);
  await contexte.close();

  // Les parcours après les captures, et jamais à leur place : un tournage qui
  // échoue en entier laisse la présentation illustrée comme avant #545.
  let videos = [];
  if (args.videos) {
    console.error(`[parcours] ${PARCOURS.length} parcours à filmer`);
    try {
      videos = await filmerParcours(navigateur, args.base, sortie);
    } catch (erreur) {
      console.error(`[parcours] ⚠ tournage abandonné : ${erreur.message}`);
    }
  } else {
    console.error("[parcours] désactivés (--sans-videos)");
  }

  await navigateur.close();

  const manifeste = {
    base: args.base,
    genere: new Date().toISOString(),
    viewport: VIEWPORT,
    pages,
    videos,
  };
  await writeFile(join(sortie, "captures.json"), `${JSON.stringify(manifeste, null, 2)}\n`, "utf8");

  const filmes = videos.filter((v) => v.fichier).length;
  if (args.videos) {
    console.error(`[parcours] ${filmes}/${videos.length} parcours filmé(s) dans ${sortie}`);
  }
  // Zéro capture = échec : l'appelant doit pouvoir enchaîner sur le repli
  // « présentation sans visuels » plutôt que de croire la série réussie. Les
  // vidéos ne pèsent pas sur ce verdict — elles s'ajoutent aux captures, elles
  // ne les remplacent pas.
  if (reussies === 0) process.exitCode = 1;
}

principal().catch((erreur) => {
  console.error(`[captures] échec : ${erreur.message}`);
  process.exitCode = 1;
});

// Coque de bureau de Maestro — processus principal (#923, docs/35 §2).
//
// Une fenêtre native remplace l'onglet de navigateur : on lance Maestro, on ne lance pas un script
// (retex du 2026-09-11, constat G6). Ce que la coque fait tient en trois gestes, et rien de plus :
//
//   1. elle DÉMARRE la stack locale — `scripts/controltower/start.sh --no-browser` ;
//   2. elle AFFICHE ce que cette stack sert, à l'origine locale et à elle seule ;
//   3. elle l'ARRÊTE quand sa fenêtre se ferme — `start.sh --stop`.
//
// ⚠ ELLE NE RÉIMPLÉMENTE NI LE DÉMARRAGE NI L'ARRÊT. `start.sh` en est la source unique, et il en
// sait déjà bien plus long que nous : préflight Redis, remplacement de la session en place, ports
// dérivés du worktree, et surtout le SOLDAGE DES RUNS EN VOL (#700, docs/28 §11) — fermer la
// fenêtre est un arrêt volontaire de la Control Tower, donc ses runs sont consignés « annulée »
// plutôt que laissés « en cours » sans écran pour les suivre. Rejouer ces gestes ici en ferait
// deux à tenir d'accord, et le second oublierait ce point-là en premier.
//
// La coque tient donc EXACTEMENT la place que tenait le chien de garde de `start.sh` (#149) : il
// ouvrait une fenêtre Chromium sur un profil jetable et attendait sa disparition pour arrêter la
// stack. Nous ouvrons notre propre fenêtre, et « sa disparition » n'est plus une déduction faite
// par sondage de processus — c'est un événement du cycle de vie de l'application.
//
// ENF-12 (docs/35 §2.5) : AUCUN embranchement de code applicatif. Le front servi ici est celui du
// mode web, au bit près — rien dans `apps/web/**` ne sait qu'il tourne dans une fenêtre, et il ne
// doit rien en savoir. Ce fichier est le seul endroit où le mot « Electron » a le droit d'exister.

'use strict';

const { app, BrowserWindow, nativeTheme, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');

const RACINE = path.resolve(__dirname, '..', '..');
const LANCEUR = path.join(RACINE, 'scripts', 'controltower', 'start.sh');

// Mêmes défauts que `start.sh`, et surtout mêmes VARIABLES : `worktree.sh ensure` les pose par
// worktree (#152), sans quoi deux sessions se disputeraient la fenêtre et les ports.
const PORT_UI = process.env.MAESTRO_PORT_UI || '3000';
const PORT_API = process.env.MAESTRO_PORT_API || '8000';

// L'URL est celle que `start.sh` ouvrirait : « localhost » et non « 127.0.0.1 ». Les deux noms
// désignent la même stack, mais pas la même ORIGINE au sens du navigateur — s'en écarter ici
// ferait diverger le stockage local de la fenêtre de celui de l'onglet, sans que rien ne le dise.
const URL_UI = `http://localhost:${PORT_UI}`;

// Ce que la fenêtre a le droit d'afficher. Tout le reste part au navigateur du système ou nulle
// part (§ « navigation »). Les deux noms de l'hôte local y sont, sur le seul port de l'UI : l'API
// n'est pas une destination de navigation, elle est appelée par la page.
const ORIGINES_LOCALES = new Set([`http://localhost:${PORT_UI}`, `http://127.0.0.1:${PORT_UI}`]);

// Sous Windows, `bash` nu peut se résoudre en bash WSL — qui ne voit pas le même système de
// fichiers, donc ne trouverait ni le dépôt ni le venv. `desktop.sh` nous passe le sien ; le repli
// ne sert qu'à un lancement direct par `npm start` depuis un shell POSIX.
const BASH = process.env.MAESTRO_BASH || 'bash';

// Les options transmises à `start.sh`, en liste blanche : ce processus reçoit aussi les arguments
// d'Electron, et laisser passer le reste enverrait `start.sh` sortir en « Option inconnue ».
const OPTIONS_STACK = new Set(['--demo', '--demonstration']);
const optionsStack = process.argv.slice(1).filter((a) => OPTIONS_STACK.has(a));

/** Quelques lignes de diagnostic, gardées pour la fenêtre d'attente quand le démarrage échoue. */
const journal = [];
function retenir(flux, donnees) {
  const texte = String(donnees);
  process[flux].write(texte);
  journal.push(texte);
  if (journal.length > 80) journal.splice(0, journal.length - 80);
}

/**
 * Joue `start.sh` avec les arguments donnés et rend son code de sortie.
 * La sortie est relayée telle quelle sur la console : c'est une coque de DÉVELOPPEMENT, lancée
 * depuis un terminal, et `start.sh` y dit des choses qu'on ne saurait pas redire mieux (le geste
 * exact pour lancer Redis, le chemin du log de l'API, ce que l'arrêt a soldé).
 */
function jouerLanceur(args) {
  return new Promise((resoudre) => {
    const enfant = spawn(BASH, [LANCEUR, ...args], {
      cwd: RACINE,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: process.env,
    });
    enfant.stdout.on('data', (d) => retenir('stdout', d));
    enfant.stderr.on('data', (d) => retenir('stderr', d));
    enfant.on('error', (err) => {
      retenir('stderr', `[coque] lancement impossible (${BASH}) : ${err.message}\n`);
      resoudre(127);
    });
    enfant.on('close', (code) => resoudre(code ?? 1));
  });
}

let fenetre = null;

/** Dit à la fenêtre d'attente où on en est. Sans effet une fois l'UI chargée — et c'est voulu. */
function annoncer(etat, message) {
  if (!fenetre || fenetre.isDestroyed()) return;
  const charge = JSON.stringify({ etat, message });
  fenetre.webContents
    .executeJavaScript(`window.maestroAttente && window.maestroAttente(${charge})`)
    .catch(() => {});
}

/**
 * Navigation : la fenêtre ne quitte JAMAIS l'origine locale (docs/19 — le modèle de menace
 * s'applique, ces réglages ne sont pas du confort).
 *
 * Ce qui est refusé ici ne disparaît pas pour autant : un lien http(s) externe part au navigateur
 * du système, où l'utilisateur l'attend. Tout autre schéma (file:, data:, …) est refusé net.
 */
function encadrerNavigation(contenu) {
  const externeOuRien = (url) => {
    let analysee;
    try {
      analysee = new URL(url);
    } catch {
      return;
    }
    if (analysee.protocol === 'http:' || analysee.protocol === 'https:') {
      shell.openExternal(url).catch(() => {});
    }
  };

  contenu.on('will-navigate', (evenement, url) => {
    let origine = null;
    try {
      origine = new URL(url).origin;
    } catch {
      /* URL illisible : traitée comme externe, donc refusée ci-dessous. */
    }
    if (origine !== null && ORIGINES_LOCALES.has(origine)) return;
    evenement.preventDefault();
    externeOuRien(url);
  });

  // `window.open` et les liens en « target=_blank » : jamais de seconde fenêtre de coque.
  contenu.setWindowOpenHandler(({ url }) => {
    externeOuRien(url);
    return { action: 'deny' };
  });

  // Une fenêtre qui perd son serveur (stack arrêtée sous ses pieds) revient à l'écran d'attente
  // plutôt qu'à la page d'erreur d'Electron, qui parlerait de codes réseau à un utilisateur.
  contenu.on('did-fail-load', (_evenement, code, description, url, principal) => {
    if (!principal || code === -3 /* abandon volontaire */) return;
    chargerAttente().then(() => annoncer('echec', `${url} ne répond plus (${description}).`));
  });
}

function chargerAttente() {
  if (!fenetre || fenetre.isDestroyed()) return Promise.resolve();
  return fenetre.loadFile(path.join(__dirname, 'attente.html')).catch(() => {});
}

function ouvrirFenetre() {
  fenetre = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: 'Maestro',
    // Le fond que la fenêtre porte AVANT d'avoir rendu quoi que ce soit. Il suit le thème du
    // système, faute de quoi un poste en thème sombre verrait un éclair blanc à chaque ouverture.
    // Les deux valeurs sont celles de `--background` dans `apps/web/app/globals.css` : la coque
    // n'apporte AUCUNE identité visuelle nouvelle (docs/35 §4), elle prolonge celle du produit.
    backgroundColor: nativeTheme.shouldUseDarkColors ? '#0a0a0a' : '#ffffff',
    show: true,
    webPreferences: {
      // Les trois réglages de sûreté du troisième critère d'acceptation. `sandbox` n'y est pas
      // nommé mais va dans le même sens, et rien ici n'a besoin qu'il soit levé : la coque
      // n'expose AUCUN pont vers la page (pas de preload, pas d'IPC) — ENF-12 veut que le front
      // servi soit celui du mode web, donc il ne peut rien attendre de nous.
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webviewTag: false,
    },
  });
  encadrerNavigation(fenetre.webContents);
  fenetre.on('closed', () => {
    fenetre = null;
  });
}

// Le démarrage en cours, pour que l'arrêt ne le double jamais : la fenêtre s'ouvre AVANT que la
// stack ne réponde (c'est la raison d'être de l'écran d'attente), donc la fermer pendant que Next
// compile est un geste ordinaire — et non un cas de bord. Sans cette attente, `--stop` libérerait
// des ports que `--no-browser` est en train de peupler, et l'ordre du résultat dépendrait de qui
// a fini le premier.
let demarrageStack = Promise.resolve(0);

async function demarrer() {
  ouvrirFenetre();
  await chargerAttente();
  annoncer('demarrage', optionsStack.length > 0 ? 'Scénario de démonstration.' : null);

  demarrageStack = jouerLanceur(['--no-browser', ...optionsStack]);
  const code = await demarrageStack;

  // La fenêtre a pu être fermée pendant le démarrage : `before-quit` s'occupe alors de l'arrêt,
  // et il n'y a plus rien à afficher.
  if (!fenetre || fenetre.isDestroyed()) return;

  if (code !== 0) {
    annoncer('echec', journal.join('').trim().split('\n').slice(-12).join('\n'));
    return;
  }
  annoncer('chargement', null);
  await fenetre.loadURL(URL_UI).catch(() => {});
}

/**
 * Fermer la fenêtre arrête la Control Tower (premier critère d'acceptation) : l'arrêt doit donc
 * avoir lieu AVANT que le processus ne meure, et `before-quit` est le seul moment où l'on peut
 * encore retenir la sortie. On le fait une fois, puis on sort par `app.exit` — repasser par
 * `app.quit` rejouerait cet événement.
 */
let arretEnCours = false;
function armerArret() {
  app.on('window-all-closed', () => app.quit());
  app.on('before-quit', (evenement) => {
    if (arretEnCours) return;
    arretEnCours = true;
    evenement.preventDefault();
    demarrageStack
      .then(() => jouerLanceur(['--stop']))
      .then((code) => app.exit(code === 0 ? 0 : 1));
  });
}

// Une seconde instance partagerait la stack de la première et l'arrêterait en se fermant. Elle
// sort donc tout de suite — par `app.exit`, avant que `before-quit` ne soit armé : passer par
// l'arrêt couperait la stack que la première fenêtre est en train de servir.
if (!app.requestSingleInstanceLock()) {
  process.stderr.write('[coque] Maestro est déjà ouvert — cette fenêtre se ferme.\n');
  app.exit(0);
} else {
  app.on('second-instance', () => {
    if (!fenetre || fenetre.isDestroyed()) return;
    if (fenetre.isMinimized()) fenetre.restore();
    fenetre.focus();
  });
  armerArret();
  app.whenReady().then(demarrer);
}

// Diagnostic : les ports servis, pour que le terminal dise d'emblée à quelle stack cette fenêtre
// est attachée — deux worktrees en ouvrent deux.
process.stdout.write(`[coque] Maestro — UI :${PORT_UI} · API :${PORT_API}\n`);

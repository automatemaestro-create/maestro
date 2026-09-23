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
// doit rien en savoir. Ce dossier est le seul endroit où le mot « Electron » a le droit d'exister.
//
// ⚠ Depuis #928 la coque expose un pont, et #938 lui ajoute son deuxième verbe. Les deux servent
// la même règle : le front teste si la fonction EXISTE, jamais où il tourne
// (`apps/web/lib/poste.ts`) — dans un onglet elle n'existe pas, et l'autre chemin reste.
//
//   - `ouvrirDossier` (#928) — montrer un dossier dans l'explorateur du système ;
//   - `montrerFichier` (#1224) — révéler un FICHIER dans son dossier, sélectionné et jamais
//     exécuté : le geste que le récit de fin d'un run pose sur chaque fichier qu'il nomme ;
//   - `choisirDossier` (#938) — ouvrir le dialogue de dossier de l'OS DANS LA FENÊTRE.
//
// Le second est la capacité que docs/35 §2.4 nommait en premier, et il vaut la peine de dire
// pourquoi il n'est PAS un doublon de `POST /api/projets/selecteur` (#278). Cette route-là ouvre
// le dialogue depuis le BACKEND, et elle porte ses limites en toutes lettres : elle refuse quand
// la requête vient du réseau (`selecteur-hors-poste` — le dialogue s'ouvrirait sur le serveur,
// devant personne) et quand le poste n'a ni PowerShell, ni `osascript`, ni `zenity`/`kdialog`
// (`selecteur-sans-outil`). Dans une fenêtre, ces deux empêchements **n'existent pas** : le
// dialogue s'ouvre là où la personne regarde, et il est celui d'Electron, pas celui d'un
// sous-process à trouver dans le PATH. C'est le premier critère d'acceptation de #938.
//
// ⚠ ET IL N'AUTORISE RIEN. Le chemin rendu ici est celui de l'OS, **non canonicalisé** : c'est la
// page qui le fait juger par `POST /api/projets/racine`, où vivent les frontières d'EF-38 (#221).
// Une seconde validation ici serait deux formules à tenir d'accord, et c'est la garde qui
// perdrait. La coque ouvre une fenêtre et lit un chemin ; elle ne dit jamais s'il est déclarable.

'use strict';

const { app, BrowserWindow, dialog, ipcMain, nativeTheme, shell } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
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

// Aucun argument de ce processus ne passe à `start.sh` : il reçoit aussi ceux d'Electron, et la
// seule option qu'on lui relayait, celle du mode démo, a quitté le produit avec lui (#1168). La coque
// joue donc deux jeux d'arguments écrits ici même, jamais autre chose.

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

/**
 * Ouvrir un dossier dans l'explorateur du système (#928) — la seule chose que la page puisse nous
 * demander, et elle est REFUSÉE par défaut.
 *
 * Trois gardes, et la troisième est celle qu'on oublie :
 *
 *   1. le chemin est une chaîne non vide et ABSOLUE — un chemin relatif serait résolu contre le
 *      répertoire de travail de la coque, c'est-à-dire la racine du dépôt ;
 *   2. il EXISTE — `openPath` sur un chemin inconnu rend une erreur que l'utilisateur ne verrait
 *      pas, là où la page peut dire « dossier introuvable » à l'endroit où il regarde ;
 *   3. c'est un DOSSIER. `shell.openPath` ouvre un fichier avec son application par défaut : sur un
 *      `.exe`, un `.bat` ou un `.lnk`, cela revient à l'EXÉCUTER. La page est servie par un serveur
 *      local qu'un autre programme du poste peut atteindre ; ce pont n'ouvre donc que des
 *      répertoires, ce qui suffit au livrable d'un run (sa racine de projet) et ne lance rien.
 *
 * Rend un booléen plutôt que de lever : la page a déjà le chemin sous les yeux et le dit elle-même
 * quand c'est non. Le détail part sur la console de la coque, qui est un terminal de développement.
 */
async function ouvrirDossier(chemin) {
  if (typeof chemin !== 'string' || chemin.trim() === '') return false;
  const cible = path.normalize(chemin);
  if (!path.isAbsolute(cible)) {
    process.stderr.write(`[coque] ouverture refusée (chemin relatif) : ${chemin}\n`);
    return false;
  }
  let etat;
  try {
    etat = fs.statSync(cible);
  } catch {
    process.stderr.write(`[coque] ouverture refusée (introuvable) : ${cible}\n`);
    return false;
  }
  if (!etat.isDirectory()) {
    process.stderr.write(`[coque] ouverture refusée (pas un dossier) : ${cible}\n`);
    return false;
  }
  // `openPath` rend la chaîne vide en cas de succès, un message d'erreur sinon.
  const erreur = await shell.openPath(cible);
  if (erreur !== '') {
    process.stderr.write(`[coque] ouverture impossible : ${erreur}\n`);
    return false;
  }
  return true;
}

/**
 * Montrer un FICHIER dans son dossier (#1224) — le troisième verbe du pont, et celui qui
 * n'exécute rien.
 *
 * `ouvrirDossier` refuse tout ce qui n'est pas un répertoire, et la raison est écrite au-dessus :
 * `shell.openPath` sur un `.exe`, un `.bat` ou un `.lnk` reviendrait à l'EXÉCUTER, alors que la
 * page est servie par un serveur local qu'un autre programme du poste peut atteindre. Ce verbe
 * lève cette borne sans lever le risque, parce qu'il ne fait pas la même chose :
 * `shell.showItemInFolder` **sélectionne** la cible dans l'explorateur et ne lance jamais rien.
 * C'est le « Reveal in File Explorer » de VS Code, et c'est le geste que le récit de fin d'un run
 * demande sur un fichier du livrable.
 *
 * Les deux premières gardes d'`ouvrirDossier` restent, et pour les mêmes raisons : chemin absolu
 * (un relatif serait résolu contre la racine du dépôt) et cible existante (l'explorateur s'ouvre
 * alors sur un dossier vide, sans rien dire, là où la page peut dire « fichier introuvable »).
 * La troisième est **inversée** : c'est un fichier qu'on attend, un dossier se montrant déjà par
 * l'autre verbe.
 *
 * Rend un booléen : `showItemInFolder` ne rapporte rien, donc le succès est « la cible existait et
 * la demande est partie ». C'est exactement ce que la page a besoin de savoir pour choisir entre
 * se taire et dire « Fichier introuvable ».
 */
function montrerFichier(chemin) {
  if (typeof chemin !== 'string' || chemin.trim() === '') return false;
  const cible = path.normalize(chemin);
  if (!path.isAbsolute(cible)) {
    process.stderr.write(`[coque] fichier non montré (chemin relatif) : ${chemin}\n`);
    return false;
  }
  let etat;
  try {
    etat = fs.statSync(cible);
  } catch {
    process.stderr.write(`[coque] fichier non montré (introuvable) : ${cible}\n`);
    return false;
  }
  if (!etat.isFile()) {
    process.stderr.write(`[coque] fichier non montré (pas un fichier) : ${cible}\n`);
    return false;
  }
  shell.showItemInFolder(cible);
  return true;
}

/**
 * Ouvrir le dialogue de dossier de l'OS **dans la fenêtre** (#938) — le second verbe du pont.
 *
 * Rend le chemin choisi, ou `null`. **Annuler n'est pas une erreur** : fermer la fenêtre est un
 * geste normal, et c'est le même contrat que `POST /api/projets/selecteur` côté backend
 * (`selecteur.choisir_dossier` rend `None`). La page ne distingue donc que deux cas, et elle n'a
 * rien à afficher sur le premier.
 *
 * `depart` (facultatif) est le dossier d'ouverture : un confort, jamais une permission — le chemin
 * **rendu** est jugé par l'API quel que soit l'endroit d'où l'on est parti. Il n'est retenu que
 * s'il est absolu, pour la même raison qu'à `ouvrirDossier` : un relatif serait résolu contre le
 * répertoire de travail de la coque, c'est-à-dire la racine du dépôt.
 *
 * `createDirectory` accompagne `openDirectory` : c'est le pendant macOS du `ShowNewFolderButton`
 * que le dialogue PowerShell pose déjà (`maestro/controltower/selecteur.py`), et il est inerte
 * ailleurs. L'origine « nouveau dossier » du formulaire de projet en dépend.
 *
 * ⚠ **Pas de verrou « un dialogue à la fois » ici**, là où le backend en a un (#278) : celui-ci a
 * pour parent `fenetre`, donc il est MODAL sur elle — rien d'autre n'est cliquable tant qu'il est
 * ouvert. Le verrou du backend existe parce que N requêtes HTTP peuvent empiler N fenêtres
 * modales que personne n'a demandées ; ici il n'y a qu'une page, et elle est bloquée.
 */
async function choisirDossier(depart) {
  if (!fenetre || fenetre.isDestroyed()) return null;
  const options = {
    title: 'Choisir le dossier du projet',
    properties: ['openDirectory', 'createDirectory'],
  };
  if (typeof depart === 'string' && depart.trim() !== '' && path.isAbsolute(depart)) {
    options.defaultPath = path.normalize(depart);
  }
  const { canceled, filePaths } = await dialog.showOpenDialog(fenetre, options);
  if (canceled || filePaths.length === 0) return null;
  return filePaths[0];
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
      // nommé mais va dans le même sens, et il reste POSÉ depuis que la coque expose un pont
      // (#928) : un preload sandboxé n'a pas accès à Node, seulement à `contextBridge` et
      // `ipcRenderer`, ce qui est exactement la surface voulue. La page reçoit une fonction, pas
      // un canal — et ENF-12 tient, le front testant une CAPACITÉ et jamais sa plateforme
      // (`apps/web/lib/poste.ts`).
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webviewTag: false,
      preload: path.join(__dirname, 'preload.js'),
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
  // Les trois canaux que la page puisse emprunter (#928, #938, #1224), armés avant qu'elle ne
  // charge. `handle` et non `on` : la page attend une réponse — si le dossier s'est ouvert, si le
  // fichier existait, quel chemin a été choisi —, et un canal à sens unique l'aurait laissée sans
  // rien à afficher.
  ipcMain.handle('maestro:ouvrir-dossier', (_evenement, chemin) => ouvrirDossier(chemin));
  ipcMain.handle('maestro:montrer-fichier', (_evenement, chemin) => montrerFichier(chemin));
  ipcMain.handle('maestro:choisir-dossier', (_evenement, depart) => choisirDossier(depart));
  ouvrirFenetre();
  await chargerAttente();
  annoncer('demarrage', null);

  demarrageStack = jouerLanceur(['--no-browser']);
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

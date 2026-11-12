// Coque de bureau de Maestro — processus principal (#923, docs/35 §2).
//
// Une fenêtre native remplace l'onglet de navigateur : on lance Maestro, on ne lance pas un script
// (retex du 2026-09-11, constat G6). Ce que la coque fait tient en trois gestes, et rien de plus :
//
//   1. elle DÉMARRE la stack locale — son lanceur, avec `--no-browser` ;
//   2. elle AFFICHE ce que cette stack sert, à l'origine locale et à elle seule ;
//   3. elle l'ARRÊTE quand sa fenêtre se ferme — le même lanceur, avec `--stop`.
//
// Installée, elle se met aussi à jour (#644) : elle télécharge la version suivante et ne la pose
// qu'après ce même arrêt (§ « la mise à jour »).
//
// Le lanceur est `scripts/controltower/start.sh` dans un clone, et le lanceur du produit
// (`python -m maestro.lanceur`, #640) dans une installation (#641) — § « deux lanceurs » plus bas.
//
// ⚠ ELLE NE RÉIMPLÉMENTE NI LE DÉMARRAGE NI L'ARRÊT. Son lanceur en est la source unique, et il
// en sait déjà bien plus long que nous : préflight du support de persistance, remplacement de la
// session en place, ports dérivés du worktree ou déplacés quand ils sont pris, et surtout le
// SOLDAGE DES RUNS EN VOL (#700, docs/28 §11) — fermer la fenêtre est un arrêt volontaire de la
// Control Tower, donc ses runs sont consignés « annulée » plutôt que laissés « en cours » sans
// écran pour les suivre. Rejouer ces gestes ici en ferait deux à tenir d'accord, et le second
// oublierait ce point-là en premier.
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
const os = require('node:os');
const path = require('node:path');

// DEUX LANCEURS, UN SEUL CONTRAT (#641). La coque d'un clone sert la stack du dépôt par
// `start.sh` — rechargement à chaud, venv et Redis du développement. La coque INSTALLÉE sert
// le produit que le paquet a posé sous `resources/produit/` par le lanceur du produit,
// `python -m maestro.lanceur` (#640), joué par le runtime Python que le paquet embarque : ni
// Python, ni Node, ni clone sur le poste. Les deux reçoivent les deux mêmes jeux d'arguments
// (`--no-browser`, `--stop`) et en font la même chose, soldage des runs compris ; la coque
// change d'exécutable et de rien d'autre.
const INSTALLEE = app.isPackaged;
const RACINE_DEPOT = path.resolve(__dirname, '..', '..');
const RACINE_PRODUIT = path.join(process.resourcesPath, 'produit');
// `python.exe` : le paquet n'est construit que pour Windows, la plateforme de référence du dépôt
// (#641 la borne là ; un installeur multi-plateforme est un second ticket).
const PYTHON_EMBARQUE = path.join(RACINE_PRODUIT, 'runtime', 'python', 'python.exe');
const RACINE = INSTALLEE ? RACINE_PRODUIT : RACINE_DEPOT;

// Où le lanceur installé inscrit la session qu'il a démarrée — `journaux_du_lanceur` de
// `maestro/emplacements.py`, sous l'état du poste : le dossier d'installation ne porte que le
// paquet, et une mise à jour l'efface (#1620). La coque installée y relit l'URL réellement
// servie : le lanceur déplace un port pris plutôt que d'échouer, et la fenêtre doit ouvrir
// l'adresse qu'il a choisie, pas celle qu'on lui demandait.
const SESSION_DU_LANCEUR = path.join(os.homedir(), '.maestro', 'lanceur', 'session.json');

// Où le produit dit sa version (#1622). Le paquet pose la coque et le produit ensemble, à la même
// version : celle de `package.json`, que l'assemblage écrit aussi à la racine du produit
// (`version.json`, `FICHIER_VERSION` de `maestro/installation.py`, que l'API sert aux Paramètres).
// Dans un clone, le produit la tire de la source même de la coque : les deux ne peuvent pas
// diverger, et la comparaison se tait (§ `ecartDeVersions`).
const VERSION_DU_PRODUIT = INSTALLEE
  ? path.join(RACINE, 'version.json')
  : path.join(RACINE, 'apps', 'desktop', 'package.json');

// Mêmes défauts que `start.sh`, et surtout mêmes VARIABLES : `worktree.sh ensure` les pose par
// worktree (#152), sans quoi deux sessions se disputeraient la fenêtre et les ports.
const PORT_UI_DEFAUT = '3000';
const PORT_API_DEFAUT = '8000';
const PORT_UI = process.env.MAESTRO_PORT_UI || PORT_UI_DEFAUT;
const PORT_API = process.env.MAESTRO_PORT_API || PORT_API_DEFAUT;

// La stack que cette fenêtre sert, nommée comme `start.sh` la nomme : son état
// (`<temp>/maestro/controltower-<api>-<ui>`, #1456), ses journaux et le profil de son navigateur
// portent ce couple, et son arrêt libère ces deux ports. C'est la clé du verrou d'instance unique
// (§ plus bas).
const STACK = `${PORT_API}-${PORT_UI}`;

// L'URL est celle que `start.sh` ouvrirait : « localhost » et non « 127.0.0.1 ». Les deux noms
// désignent la même stack, mais pas la même ORIGINE au sens du navigateur — s'en écarter ici
// ferait diverger le stockage local de la fenêtre de celui de l'onglet, sans que rien ne le dise.
const URL_UI = `http://localhost:${PORT_UI}`;

// Ce que la fenêtre a le droit d'afficher. Tout le reste part au navigateur du système ou nulle
// part (§ « navigation »). Les deux noms de l'hôte local y sont, sur le seul port de l'UI : l'API
// n'est pas une destination de navigation, elle est appelée par la page.
function originesLocales(port) {
  return new Set([`http://localhost:${port}`, `http://127.0.0.1:${port}`]);
}
// Celles du port DEMANDÉ, jusqu'à ce que le lanceur installé dise celui qu'il a servi (§ `servir`).
let ORIGINES_LOCALES = originesLocales(PORT_UI);

// Sous Windows, `bash` nu peut se résoudre en bash WSL — qui ne voit pas le même système de
// fichiers, donc ne trouverait ni le dépôt ni le venv. `desktop.sh` nous passe le sien ; le repli
// ne sert qu'à un lancement direct par `npm start` depuis un shell POSIX.
const BASH = process.env.MAESTRO_BASH || 'bash';

// Le lanceur que la coque joue, et le seul (§ « deux lanceurs, un seul contrat » plus haut).
const LANCEUR = INSTALLEE
  ? { executable: PYTHON_EMBARQUE, prefixe: ['-m', 'maestro.lanceur'] }
  : { executable: BASH, prefixe: [path.join(RACINE, 'scripts', 'controltower', 'start.sh')] };

// Aucun argument de ce processus ne passe au lanceur : il reçoit aussi ceux d'Electron, et la
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
 * Joue le lanceur avec les arguments donnés et rend son code de sortie.
 * La sortie est relayée telle quelle sur la console, et retenue pour l'écran d'attente : le
 * lanceur y dit des choses qu'on ne saurait pas redire mieux (ce qui manque pour démarrer, le
 * chemin du journal d'un service mort, ce que l'arrêt a soldé).
 *
 * `windowsHide` : la coque installée n'a pas de console, et sans lui Windows en ouvrirait une
 * pour l'interpréteur du lanceur — une fenêtre noire à côté de Maestro, qui l'arrêterait si on
 * la fermait.
 */
function jouerLanceur(args) {
  return new Promise((resoudre) => {
    const enfant = spawn(LANCEUR.executable, [...LANCEUR.prefixe, ...args], {
      cwd: RACINE,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: process.env,
      windowsHide: true,
    });
    enfant.stdout.on('data', (d) => retenir('stdout', d));
    enfant.stderr.on('data', (d) => retenir('stderr', d));
    enfant.on('error', (err) => {
      retenir('stderr', `[coque] lancement impossible (${LANCEUR.executable}) : ${err.message}\n`);
      resoudre(127);
    });
    enfant.on('close', (code) => resoudre(code ?? 1));
  });
}

/**
 * L'URL que la fenêtre ouvre une fois la stack démarrée.
 *
 * Dans un clone, celle que `start.sh` ouvrirait (§ `URL_UI`). Installée, celle que le lanceur
 * du produit a INSCRITE : il déplace un port pris plutôt que d'échouer (#640), et c'est
 * l'adresse qu'il a choisie qui sert. Les origines admises suivent, puisque c'est là que la
 * fenêtre a désormais le droit d'aller. Une session illisible retombe sur l'URL demandée — le
 * lanceur vient de rendre `0`, il sert donc quelque part, et le pire est la page d'attente.
 */
function urlServie() {
  if (!INSTALLEE) return URL_UI;
  try {
    const { url } = JSON.parse(fs.readFileSync(SESSION_DU_LANCEUR, 'utf8'));
    const lue = new URL(url);
    ORIGINES_LOCALES = originesLocales(lue.port);
    return lue.href;
  } catch (err) {
    retenir('stderr', `[coque] session du lanceur illisible (${err.message}) — ${URL_UI}\n`);
    return URL_UI;
  }
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
 * La coque et le produit qu'elle lance ont-ils la même version (#1622) ? Rend `null` quand oui,
 * sinon les deux versions — celle du produit vaut `null` quand elle ne se lit pas, avec le relevé
 * qui dit pourquoi : une installation qui ne sait pas dire ce qu'elle lance est aussi incohérente
 * qu'une installation à moitié mise à jour.
 */
function ecartDeVersions() {
  const coque = app.getVersion();
  let produit = null;
  let releve = '';
  try {
    const { version } = JSON.parse(fs.readFileSync(VERSION_DU_PRODUIT, 'utf8'));
    if (typeof version === 'string' && version.trim() !== '') produit = version.trim();
    else releve = `${path.basename(VERSION_DU_PRODUIT)} ne porte pas de version`;
  } catch (err) {
    releve = err.message;
  }
  if (produit === coque) return null;
  return { coque, produit, releve };
}

/**
 * Dire l'écart dans la fenêtre, par un dialogue natif — et seulement le dire.
 *
 * Le front n'en sait rien et ne doit rien en savoir (ENF-12) : il lit la version du produit dans
 * l'API, jamais celle de la coque. C'est donc ici que l'écart se voit, et nulle part ailleurs. La
 * page d'attente ne le porte pas : elle disparaît dès que l'interface répond, et rien n'y doit
 * grandir. Le dialogue reste, lui, jusqu'à ce qu'on l'ait lu.
 *
 * Maestro démarre quand même : l'écart est un constat, pas un verrou (docs/41). Il n'est pas
 * attendu, et la ligne part aussi au journal de la coque — que la fenêtre d'attente montre si le
 * démarrage échoue, où elle explique peut-être pourquoi.
 */
function signalerEcart(ecart) {
  const produit = ecart.produit === null ? 'illisible' : ecart.produit;
  const releve = ecart.releve === '' ? '' : ` (${ecart.releve})`;
  retenir(
    'stderr',
    `[coque] installation incohérente — coque ${ecart.coque}, produit ${produit}${releve}\n`,
  );
  if (!fenetre || fenetre.isDestroyed()) return;
  dialog
    .showMessageBox(fenetre, {
      type: 'warning',
      title: 'Maestro',
      message:
        ecart.produit === null
          ? 'Maestro ne peut pas lire la version du produit que cette fenêtre lance.'
          : "Cette fenêtre et le produit qu'elle lance n'ont pas la même version.",
      detail:
        `Fenêtre de Maestro : ${ecart.coque}\nProduit : ${produit}${releve}\n\n` +
        'Maestro démarre quand même, mais une installation à moitié mise à jour peut mal se ' +
        'comporter. Réinstallez Maestro : son installeur pose les deux ensemble.',
      buttons: ['Compris'],
      noLink: true,
    })
    .catch(() => {});
}

// LA MISE À JOUR (#644, docs/54 §6). Une installation se met à jour seule : elle demande à son
// canal s'il y a plus récent, le télécharge en arrière-plan, et le POSE quand la personne le
// décide — « Redémarrer maintenant », ou la prochaine fermeture de la fenêtre. Jamais pendant que
// Maestro sert : l'installeur efface le dossier d'installation (#1620), donc la stack doit être
// arrêtée — ses runs soldés — avant qu'il ne parte. Cet arrêt est celui de la fermeture, joué par
// le même lanceur (`--stop`, § `armerArret`) : la mise à jour ne connaît aucun autre chemin pour
// éteindre Maestro.
//
// Le mécanisme est `electron-updater`, un adaptateur : il lit le canal, vérifie l'empreinte
// (sha512 de `latest.yml`) et lance l'installeur. Ce qui est installé, quand, et ce qui s'arrête
// avant, se décide ici.
//
// Le canal est celui du paquet (`build.publish` de `package.json`, que l'empaquetage écrit dans
// `resources/app-update.yml`) : les publications du dépôt sur GitHub. `MAESTRO_MISES_A_JOUR_FLUX`
// le remplace par une adresse, ou l'éteint (`0`) — la vérification du paquet sert ainsi son propre
// canal, sur le poste (`scripts/distribution/verifier.sh --mise-a-jour`). Dans un clone, rien :
// un clone se met à jour par git.
const VARIABLE_FLUX = 'MAESTRO_MISES_A_JOUR_FLUX';
// Au lancement, puis toutes les six heures : une fenêtre qui reste ouverte des jours apprend
// quand même qu'une version est sortie.
const INTERVALLE_DES_VERIFICATIONS_MS = 6 * 60 * 60 * 1000;
// `http:` n'est admis que vers le poste lui-même : ce qu'on télécharge là s'exécute, et seul le
// chiffrement de bout en bout garde un canal distant de la substitution.
const HOTES_LOCAUX = new Set(['localhost', '127.0.0.1', '[::1]']);

/**
 * Le canal que la variable désigne : `{ actif, url }` — `url` à `null` pour celui du paquet —,
 * ou `{ actif: false, raison }`. Une valeur qu'on ne sait pas lire ÉTEINT la mise à jour en le
 * disant, plutôt que de retomber en silence sur le canal du paquet : qui a posé la variable
 * voulait autre chose que lui.
 */
function fluxDesMisesAJour(valeur) {
  const brut = (valeur ?? '').trim();
  if (brut === '') return { actif: true, url: null };
  if (brut === '0') return { actif: false, raison: `${VARIABLE_FLUX}=0` };
  let adresse = null;
  try {
    adresse = new URL(brut);
  } catch {
    /* illisible : refusée ci-dessous. */
  }
  const admise =
    adresse !== null &&
    (adresse.protocol === 'https:' ||
      (adresse.protocol === 'http:' && HOTES_LOCAUX.has(adresse.hostname)));
  if (admise) return { actif: true, url: adresse.href };
  return {
    actif: false,
    raison: `${VARIABLE_FLUX} refusée (ni https, ni http vers le poste) : ${brut}`,
  };
}

// Le module de mise à jour une fois armé, la version téléchargée et prête à poser, la dernière
// proposée (une vérification qui retrouve la même ne la repropose pas), et ce que la personne a
// demandé : un redémarrage, ou rien — l'installation attend alors la fermeture.
let miseAJour = null;
let versionPrete = null;
let versionProposee = null;
let redemarrageDemande = false;

function direDeLaMiseAJour(flux, texte) {
  retenir(flux, `[mise à jour] ${texte}\n`);
}

/**
 * Arme la mise à jour d'une installation. Appelée dès que la fenêtre est ouverte, AVANT le
 * démarrage de la stack et sans l'attendre : une version qui ne démarre plus doit encore pouvoir
 * recevoir celle qui la corrige — le dialogue s'ouvre alors par-dessus l'écran d'échec.
 *
 * Une vérification qui échoue (poste hors ligne, canal muet) se dit au journal et rien de plus :
 * c'est un cas ordinaire, et la suivante repassera.
 */
function armerMisesAJour() {
  if (!INSTALLEE) return;
  const flux = fluxDesMisesAJour(process.env[VARIABLE_FLUX]);
  if (!flux.actif) {
    direDeLaMiseAJour('stdout', `éteinte — ${flux.raison}`);
    return;
  }
  const { autoUpdater } = require('electron-updater');
  autoUpdater.logger = {
    info: (m) => direDeLaMiseAJour('stdout', m),
    warn: (m) => direDeLaMiseAJour('stdout', m),
    error: (m) => direDeLaMiseAJour('stderr', m),
    debug: () => {},
  };
  autoUpdater.autoDownload = true;
  // L'installation à la sortie est la nôtre (§ `installerLaMiseAJour`) : celle du module partirait
  // sur l'événement `quit`, sans attendre que la stack soit arrêtée.
  autoUpdater.autoInstallOnAppQuit = false;
  // Toujours vers la plus récente, jamais en arrière : les migrations du journal (#1621) savent
  // rattraper des versions sautées, et un fichier qu'une version plus récente a mis à niveau est
  // refusé par une plus ancienne.
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  // Le téléchargement différentiel cherche la carte de blocs de l'installeur EN PLACE à l'adresse
  // du canal ; `releases/latest/download` ne sert que la dernière. On télécharge donc l'installeur
  // entier — et l'empreinte de `latest.yml` le vérifie de toute façon.
  autoUpdater.disableDifferentialDownload = true;
  if (flux.url !== null) autoUpdater.setFeedURL({ provider: 'generic', url: flux.url });
  autoUpdater.on('error', (err) => direDeLaMiseAJour('stderr', `impossible : ${err.message}`));
  autoUpdater.on('update-downloaded', (info) => {
    versionPrete = info.version;
    direDeLaMiseAJour('stdout', `prête : ${info.version}`);
    proposerLeRedemarrage(info.version);
  });
  miseAJour = autoUpdater;
  // L'échec est déjà dit par l'événement `error` ; la promesse ne doit pas le redire en rejet.
  const verifier = () => autoUpdater.checkForUpdates().catch(() => {});
  verifier();
  setInterval(verifier, INTERVALLE_DES_VERIFICATIONS_MS);
}

/**
 * Proposer de redémarrer sur la version téléchargée — par un dialogue natif, pour la raison qui
 * vaut à `signalerEcart` : le front n'en sait rien (ENF-12).
 *
 * « Plus tard » est le défaut, et fermer le dialogue y revient : rien ne s'interrompt sans un
 * geste. Le texte dit ce que coûte le redémarrage, parce qu'il coûte ce que coûte la fermeture.
 */
function proposerLeRedemarrage(version) {
  if (arretEnCours || version === versionProposee) return;
  if (!fenetre || fenetre.isDestroyed()) return;
  versionProposee = version;
  dialog
    .showMessageBox(fenetre, {
      type: 'info',
      title: 'Maestro',
      message: `Maestro ${version} est prête à s'installer.`,
      detail:
        "Redémarrer maintenant arrête Maestro comme la fermeture de sa fenêtre : un run en cours " +
        "s'arrête avec lui, et se reprend après le redémarrage (« Reprendre »). Vos projets, " +
        'vos réglages et votre historique sont conservés.\n\n' +
        "Plus tard : la mise à jour s'installera quand vous fermerez Maestro.",
      buttons: ['Redémarrer maintenant', 'Plus tard'],
      defaultId: 1,
      cancelId: 1,
      noLink: true,
    })
    .then(({ response }) => {
      if (response !== 0 || arretEnCours) return;
      redemarrageDemande = true;
      direDeLaMiseAJour('stdout', `redémarrage demandé sur ${version} — arrêt de Maestro d'abord`);
      // Le chemin de la fermeture, et lui seul : `before-quit` arrête la stack, puis installe.
      app.quit();
    })
    .catch(() => {});
}

/**
 * Après l'arrêt de la stack : poser la version prête, s'il y en a une. Rend `true` quand
 * l'installeur est parti — la coque n'a plus alors qu'à sortir —, `false` quand il n'y a rien à
 * poser ou qu'on ne le pose pas.
 *
 * Rien ne se pose sur un arrêt qui n'a pas abouti : une stack à demi arrêtée tient encore des
 * fichiers du dossier que l'installeur effacerait. La version reste téléchargée, et le prochain
 * lancement la repropose.
 *
 * Sur un redémarrage, l'installeur montre sa progression et relance Maestro ; à la fermeture, il
 * pose la version en silence et ne relance rien — la personne a fermé Maestro.
 */
function installerLaMiseAJour(codeArret) {
  if (miseAJour === null || versionPrete === null) return false;
  if (codeArret !== 0) {
    direDeLaMiseAJour('stderr', `${versionPrete} non installée : l'arrêt a rendu ${codeArret}`);
    return false;
  }
  const relancer = redemarrageDemande;
  direDeLaMiseAJour(
    'stdout',
    `installation de ${versionPrete}${relancer ? ', puis redémarrage' : ', en silence'}`,
  );
  if (!miseAJour.install(!relancer, relancer)) return false;
  // L'installeur est détaché : il survit à la coque, et attend qu'elle soit sortie pour poser.
  setImmediate(() => app.exit(0));
  return true;
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

  // Avant de lancer le produit, et sans l'attendre : le dialogue reste ouvert pendant que la
  // stack démarre derrière lui (§ `signalerEcart`).
  const ecart = ecartDeVersions();
  if (ecart !== null) signalerEcart(ecart);
  armerMisesAJour();

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
  await fenetre.loadURL(urlServie()).catch(() => {});
}

/**
 * Fermer la fenêtre arrête la Control Tower (premier critère d'acceptation) : l'arrêt doit donc
 * avoir lieu AVANT que le processus ne meure, et `before-quit` est le seul moment où l'on peut
 * encore retenir la sortie. On le fait une fois, puis on sort par `app.exit` — repasser par
 * `app.quit` rejouerait cet événement.
 *
 * C'est aussi le seul chemin d'une mise à jour (#644) : « Redémarrer maintenant » passe par
 * `app.quit`, donc par ici, et la version prête ne se pose qu'une fois la stack arrêtée.
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
      .then((code) => {
        if (installerLaMiseAJour(code)) return;
        app.exit(code === 0 ? 0 : 1);
      });
  });
}

// Le profil de la fenêtre, et avec lui le VERROU D'INSTANCE UNIQUE (#1275). Electron tient ce
// verrou sur le dossier `userData`, qui vaut `%APPDATA%/desktop` (le nom du paquet) pour TOUTES
// les copies du dépôt tant que rien ne le fixe : une fenêtre du clone principal (:3000) refusait
// donc celle d'un worktree (:3073), alors que chaque worktree a sa stack (docs/10 §9, #1164). Le
// verrou garde ce qu'une seconde instance casserait — LA STACK —, donc il en prend la clé.
//
// Pas la racine de la copie : deux copies sur les mêmes ports sont UNE stack, et l'arrêt de l'une
// (qui libère les ports) couperait celle de l'autre ; une copie sur deux couples en porte deux, que
// `start.sh` sait tenir séparées.
//
// La stack par défaut garde le profil qu'elle a toujours eu : le stockage local de la fenêtre y
// vit — thème, projet actif, brouillons, guide déjà vu —, et le déplacer l'effacerait sans rien
// dire. Toute autre stack prend le sien, à part. Fixé AVANT le verrou : après, il serait déjà pris
// sur le dossier commun.
if (STACK !== `${PORT_API_DEFAUT}-${PORT_UI_DEFAUT}`) {
  app.setPath('userData', path.join(app.getPath('appData'), 'maestro-coque', STACK));
}

// Une seconde instance DE LA MÊME STACK partagerait les ports de la première et l'arrêterait en se
// fermant. Elle sort donc tout de suite — par `app.exit`, avant que `before-quit` ne soit armé :
// passer par l'arrêt couperait la stack que la première fenêtre est en train de servir.
if (!app.requestSingleInstanceLock()) {
  process.stderr.write(
    `[coque] Maestro est déjà ouvert sur cette stack (UI :${PORT_UI} · API :${PORT_API}) — ` +
      'sa fenêtre revient au premier plan, celle-ci se ferme.\n',
  );
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
// est attachée — deux worktrees, donc deux couples de ports, en ouvrent deux (#1275).
process.stdout.write(`[coque] Maestro — UI :${PORT_UI} · API :${PORT_API}\n`);

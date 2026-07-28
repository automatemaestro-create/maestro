#!/usr/bin/env node
// Lanceur du serveur MCP `chrome-maestro` (ticket #153, parent #144).
//
// Claude Code démarre les serveurs de .mcp.json avec le PATH de SON processus — pas celui d'un
// shell interactif. Un `nvm use 18` fait donc démarrer @playwright/mcp sous Node 18, qui exige
// Node 20+ : le serveur meurt au lancement et Claude Code l'affiche « not connected », sans
// indiquer pourquoi. Aucun hook de shell (`.nvmrc`, fnm) ne corrige ce cas : ils se déclenchent
// au `cd`, or il n'y a pas de shell dans la chaîne.
//
// Ce lanceur s'interpose : quel que soit le Node qui l'exécute, il relance @playwright/mcp avec le
// Node ÉPINGLÉ par le dépôt (.node-version, provisionné sous .tools/ par scripts/setup.sh).
// Il est volontairement écrit pour tourner sous un Node ancien — pas de syntaxe récente, pas de
// dépendance : c'est précisément le Node périmé qui l'exécutera.
//
// .mcp.json l'invoque via `command: "node"` et non `bash` : sous Windows, `bash` peut se résoudre
// en bash WSL, qui ne voit pas le même système de fichiers. `node` est déjà la dépendance implicite
// de l'ancien `command: "npx"`, donc on n'ajoute aucun prérequis.

import { spawn } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const RACINE = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const EST_WINDOWS = process.platform === 'win32';

/** Version épinglée par le dépôt, ou null si .node-version est absent/illisible. */
function versionEpinglee() {
  try {
    const brut = readFileSync(join(RACINE, '.node-version'), 'utf8').trim();
    return brut.replace(/^v/, '') || null;
  } catch {
    return null;
  }
}

/** Exécutable du Node provisionné sous .tools/node/, ou null s'il n'est pas là. */
function nodeDuDepot() {
  const version = versionEpinglee();
  if (!version) return null;
  const racine = join(RACINE, '.tools', 'node', `v${version}`);
  const exe = EST_WINDOWS ? join(racine, 'node.exe') : join(racine, 'bin', 'node');
  return existsSync(exe) ? exe : null;
}

/** CLI de @playwright/mcp installé localement par setup.sh, ou null. */
function cliLocal() {
  const cli = join(RACINE, '.tools', 'mcp', 'node_modules', '@playwright', 'mcp', 'cli.js');
  return existsSync(cli) ? cli : null;
}

/**
 * Le `npx-cli.js` livré avec un Node donné. On vise ce fichier JS plutôt que le lanceur `npx` :
 * sous Windows `npx` est un `.cmd`, que spawn ne sait exécuter qu'avec `shell: true` — ce qui
 * rouvrirait des soucis de quoting. Un .js se passe à node directement, partout pareil.
 * La disposition diffère selon la plateforme : node.exe est à la racine sous Windows, dans bin/
 * ailleurs, et npm vit respectivement dans node_modules/ et lib/node_modules/.
 */
function npxDe(nodeExe) {
  const base = dirname(nodeExe);
  const candidats = [
    join(base, 'node_modules', 'npm', 'bin', 'npx-cli.js'),          // Windows
    join(base, '..', 'lib', 'node_modules', 'npm', 'bin', 'npx-cli.js'), // Unix
  ];
  return candidats.find((c) => existsSync(c)) ?? null;
}

// Le Node du dépôt est le chemin nominal ; à défaut, on garde celui qui nous exécute — mieux vaut
// un serveur qui tente de démarrer (et dont l'erreur sera lisible) qu'un lanceur qui refuse.
const node = nodeDuDepot() ?? process.execPath;
const cli = cliLocal();
const args = process.argv.slice(2);

// Sans paquet local (setup.sh pas encore passé), on retombe sur npx : le serveur reste utilisable
// sur un clone frais, au prix d'une résolution réseau au premier lancement.
let argv;
if (cli) {
  argv = [cli, ...args];
} else {
  const npx = npxDe(node);
  if (!npx) {
    process.stderr.write(
      '[chrome-maestro] ni .tools/mcp/ ni npx trouvés — lancer : bash scripts/setup.sh --only node\n',
    );
    process.exit(1);
  }
  argv = [npx, '-y', `@playwright/mcp`, ...args];
}
const commande = [node, argv];

const enfant = spawn(commande[0], commande[1], {
  cwd: RACINE,
  // Transport MCP = stdio : les flux doivent traverser ce lanceur sans être touchés.
  stdio: 'inherit',
  // Le Node du dépôt d'abord, pour que le serveur et ses éventuels sous-processus le retrouvent.
  env: { ...process.env, PATH: `${dirname(node)}${EST_WINDOWS ? ';' : ':'}${process.env.PATH ?? ''}` },
});

enfant.on('error', (err) => {
  process.stderr.write(`[chrome-maestro] lancement impossible (${node}) : ${err.message}\n`);
  process.exit(1);
});

// On reproduit la sortie de l'enfant, signal compris : Claude Code doit voir le vrai verdict.
enfant.on('exit', (code, signal) => {
  if (signal) process.kill(process.pid, signal);
  else process.exit(code ?? 0);
});

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => enfant.kill(signal));
}

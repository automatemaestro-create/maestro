// Capture un échantillon **verbatim** du registre MCP officiel (#680, lot 6/6 de #673).
//
// Pourquoi ce script existe : les tests de la traduction (#676) doivent jouer sur
// des `server.json` **réels**, et aucun test ne doit parler au registre en direct
// — il est en préversion, « no uptime or data durability guarantees », et un
// rouge dû à son indisponibilité n'apprend rien. Le corpus est donc capturé une
// fois, versionné, et relu hors ligne. Ce script est ce qui rend la capture
// **rejouable** : un corpus que personne ne sait refaire est un corpus qui rote.
//
// Usage : node tests/fixtures/mcp_amont/capturer.mjs [pages]
// Écrit `corpus.jsonl` à côté de lui — une **enveloppe de listing par ligne**
// (`{"server": …, "_meta": …}`), exactement comme l'amont l'a servie. Aucun champ
// n'est réécrit, réordonné ni élagué : ce que le corpus perd, il le perd en
// cessant d'être un échantillon.
//
// La sélection vise la **couverture des formes**, pas la représentativité
// statistique : les deux millésimes de schéma en circulation, les deux transports
// distants, les registres de paquets supportés et non supportés, les variables en
// `env` comme en **argv** (l'échantillon fautif dont le refus `variable_en_argv`
// doit être prouvé), et les trois statuts amont.

import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const AMONT = "https://registry.modelcontextprotocol.io/v0.1/servers";
const PAGES = Number(process.argv[2] || 40);
const PAR_CATEGORIE = 4;

const ici = dirname(fileURLToPath(import.meta.url));

async function page(params) {
  const reponse = await fetch(`${AMONT}?${new URLSearchParams(params)}`);
  if (!reponse.ok) throw new Error(`${reponse.status} ${await reponse.text()}`);
  return reponse.json();
}

async function moissonner(params, pages) {
  const vues = [];
  let curseur = "";
  for (let n = 0; n < pages; n += 1) {
    const charge = await page(curseur ? { ...params, cursor: curseur } : params);
    vues.push(...(charge.servers || []));
    curseur = charge.metadata?.nextCursor || "";
    if (!curseur) break;
  }
  return vues;
}

const millesime = (s) => (s?.$schema || "").split("/").at(-2) || "";
const meta = (e) => e?._meta?.["io.modelcontextprotocol.registry/official"] || {};
const gabarite = (v) => typeof v === "string" && v.includes("{");

// Un argument porteur de variable : c'est exactement ce que la traduction refuse
// (`maestro.agents.mcp.resolus` ne résout `${VAR}` que dans `env` et `headers`,
// jamais dans `args`). On le cherche à la source plutôt que de le fabriquer.
function argvVariable(serveur) {
  for (const paquet of serveur.packages || []) {
    for (const arg of [...(paquet.packageArguments || []), ...(paquet.runtimeArguments || [])]) {
      if (gabarite(arg?.value) || gabarite(arg?.name) || arg?.variables) return true;
    }
  }
  return false;
}

function categories(entree) {
  const serveur = entree.server || {};
  const officiel = meta(entree);
  const distants = serveur.remotes || [];
  const paquets = serveur.packages || [];
  const noms = [];
  const schema = millesime(serveur);
  if (schema) noms.push(`schema:${schema}`);
  for (const distant of distants) noms.push(`remote:${distant.type}`);
  for (const paquet of paquets) {
    noms.push(`package:${paquet.registryType}`);
    if ((paquet.environmentVariables || []).some((v) => v.isSecret)) noms.push("env:secret");
    if ((paquet.environmentVariables || []).some((v) => !v.isSecret)) noms.push("env:public");
    if ((paquet.packageArguments || []).length) noms.push("argv:package");
    if ((paquet.runtimeArguments || []).length) noms.push("argv:runtime");
  }
  if (argvVariable(serveur)) noms.push("argv:variable");
  if (!distants.length && !paquets.length) noms.push("sans-forme");
  if (distants.length && paquets.length) noms.push("mixte");
  noms.push(`statut:${officiel.status || "?"}`);
  return noms;
}

const brut = [
  // Le gros du corpus : la tête du catalogue, telle que l'amont la sert.
  ...(await moissonner({ version: "latest", limit: "100" }, PAGES)),
  // Une fenêtre incrémentale : c'est là que vivent les `deleted` et les
  // `deprecated`, que le listing par défaut ne sert pas.
  ...(await moissonner(
    { version: "latest", limit: "100", updated_since: "2026-01-01T00:00:00Z", include_deleted: "true" },
    10,
  )),
];

const parNom = new Map();
for (const entree of brut) {
  const nom = entree?.server?.name;
  if (nom && !parNom.has(nom)) parNom.set(nom, entree);
}

const comptes = new Map();
const retenues = new Map();
for (const [nom, entree] of parNom) {
  for (const categorie of categories(entree)) {
    const vus = comptes.get(categorie) || 0;
    if (vus >= PAR_CATEGORIE) continue;
    comptes.set(categorie, vus + 1);
    retenues.set(nom, entree);
  }
}

const lignes = [...retenues.entries()]
  .sort(([a], [b]) => (a < b ? -1 : 1))
  .map(([, entree]) => JSON.stringify(entree));
writeFileSync(join(ici, "corpus.jsonl"), `${lignes.join("\n")}\n`, "utf8");

console.log(`${parNom.size} entrées vues, ${lignes.length} retenues`);
console.log([...comptes.entries()].sort().map(([c, n]) => `  ${c} : ${n}`).join("\n"));

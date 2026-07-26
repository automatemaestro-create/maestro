/**
 * Génère les icônes applicatives binaires de la Control Tower (#120) à partir
 * du monogramme Maestro — la même géométrie que `components/Logo.tsx` et
 * `app/icon.svg`. Produit :
 *   - `app/favicon.ico`  : conteneur ICO multi-tailles (16/32/48 px, PNG embarqués),
 *                          repli des vieux navigateurs qui ignorent le SVG ;
 *   - `app/apple-icon.png`: 180×180, écran d'accueil iOS (tuile pleine, jamais transparente).
 *
 * Le SVG source est ici en couleurs **fixes** (tuile sombre + mark clair) : un
 * favicon rastérisé n'a pas de contexte de page, et ce couple reste lisible sur
 * un onglet clair (tuile qui tranche) comme sombre (le « M » clair qui ressort).
 *
 * Dépendance : `sharp` (déjà présent, tiré par Next). Régénération :
 *   node scripts/build-icons.mjs
 */

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { writeFileSync } from "node:fs";

import sharp from "sharp";

const ICI = dirname(fileURLToPath(import.meta.url));
const APP = join(ICI, "..", "app");

const TUILE = "#171717";
const MARK = "#fafafa";

/** Le monogramme, décliné à la taille voulue (viewBox 32, agrandi par attributs). */
const svg = (taille) => `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="${taille}" height="${taille}">
  <rect width="32" height="32" rx="7" fill="${TUILE}"/>
  <path d="M7 24V10l9 8 9-8v14" fill="none" stroke="${MARK}" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="16" cy="6.75" r="2.1" fill="${MARK}"/>
</svg>`;

const png = (taille) => sharp(Buffer.from(svg(taille))).png().toBuffer();

/** Assemble plusieurs PNG en un conteneur ICO (chaque entrée est un PNG embarqué). */
function assemblerIco(images) {
  const enTete = Buffer.alloc(6);
  enTete.writeUInt16LE(0, 0); // réservé
  enTete.writeUInt16LE(1, 2); // type : 1 = icône
  enTete.writeUInt16LE(images.length, 4);

  const entrees = [];
  const blobs = [];
  let offset = 6 + images.length * 16;
  for (const { taille, data } of images) {
    const e = Buffer.alloc(16);
    e.writeUInt8(taille >= 256 ? 0 : taille, 0); // largeur (0 = 256)
    e.writeUInt8(taille >= 256 ? 0 : taille, 1); // hauteur
    e.writeUInt8(0, 2); // palette
    e.writeUInt8(0, 3); // réservé
    e.writeUInt16LE(1, 4); // plans
    e.writeUInt16LE(32, 6); // bits/pixel
    e.writeUInt32LE(data.length, 8);
    e.writeUInt32LE(offset, 12);
    entrees.push(e);
    blobs.push(data);
    offset += data.length;
  }
  return Buffer.concat([enTete, ...entrees, ...blobs]);
}

const tailles = [16, 32, 48];
const images = await Promise.all(
  tailles.map(async (taille) => ({ taille, data: await png(taille) })),
);
writeFileSync(join(APP, "favicon.ico"), assemblerIco(images));
writeFileSync(join(APP, "apple-icon.png"), await png(180));

console.log("favicon.ico (16/32/48) + apple-icon.png (180) régénérés.");

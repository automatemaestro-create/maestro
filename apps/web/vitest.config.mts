/**
 * Le harnais de test de la Control Tower (#124, lot 8 de la refonte UX #116).
 *
 * Les lots 1 à 7 ont livré leur interface sans tests — c'est la convention de
 * découpage du projet (docs/10 §5.1 : « tests différés au lot final »). Ce
 * fichier pose l'outillage qui manquait à `apps/web`, jusque-là vérifié par le
 * seul couple `eslint` + `next build` : celui-ci attrape une faute de typage,
 * pas une régression de comportement.
 *
 * Vitest plutôt que Jest : le projet est déjà sur l'outillage Vite/ESM de Next,
 * et Vitest lit `tsconfig`/JSX sans transformateur supplémentaire à maintenir.
 * L'environnement `jsdom` suffit — ces tests portent sur la logique et le rendu,
 * pas sur le rendu pixel ; le bout en bout dans un vrai navigateur reste le
 * rôle du skill `/verify`.
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const racine = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react()],
  // Le même alias que `tsconfig.json` (`@/*` → `./*`) : les tests importent les
  // modules sous le nom que le code applicatif emploie, pas en chemin relatif.
  resolve: { alias: { "@": racine } },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    include: ["tests/**/*.test.ts", "tests/**/*.test.tsx"],
    // Les espions posés par un test ne fuient pas dans le suivant.
    restoreMocks: true,
    // Le pendant de l'`asyncUtilTimeout` de `tests/setup.ts` : un test doit
    // pouvoir dépenser l'attente qu'on lui accorde. Laissé à 5 s (le défaut), il
    // couperait *pendant* le `waitFor` et rendrait « test timed out » à la place
    // du « Unable to find role… » et de sa capture du DOM — soit le message qui
    // dit quoi corriger remplacé par celui qui dit seulement que c'est long. Un
    // test réellement bloqué échoue toujours en premier sur son attente, donc ce
    // plafond ne rallonge pas une suite rouge.
    testTimeout: 15_000,
  },
});

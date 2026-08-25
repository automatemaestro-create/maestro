import { defineConfig, globalIgnores } from "eslint/config";
import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
import jsxA11y from "eslint-plugin-jsx-a11y";

/**
 * Le lint d'accessibilité (#537, lot 5 de #532).
 *
 * `next/core-web-vitals` n'active que **6 règles `jsx-a11y` sur ~36, toutes en
 * `warn`** (docs/30 §3.4) — et un `warn` ne fait pas rougir un pipeline, donc ne
 * garde rien. Le preset `recommended` du plugin les porte **toutes**, et à
 * `error` : c'est sa valeur par défaut, on ne la réécrit pas ici, on la laisse
 * arriver. Ce qui rend la ligne ci-dessous suffisante est sa **place** — après
 * `nextVitals`, donc les six `warn` qu'il pose sont écrasés par les `error` du
 * preset ; l'inverse rendrait le contraire.
 *
 * ⚠ On reprend la **table de règles** du preset, jamais le preset entier : son
 * bloc déclare aussi `plugins: { "jsx-a11y": … }`, et `nextVitals` a déjà
 * enregistré ce nom-là. ESLint 9 refuse alors la configuration en bloc —
 * « Cannot redefine plugin "jsx-a11y" » —, avant d'avoir analysé une ligne.
 * Reprendre `.rules` garde ce qui compte (les ~36 règles à leur niveau
 * recommandé) et laisse le plugin à son unique déclarant ; recopier la liste à
 * la main l'aurait figée à la version du jour, alors qu'elle suit ici les
 * montées de `eslint-plugin-jsx-a11y`.
 *
 * Le bloc est **restreint aux fichiers qui portent du JSX** : une règle
 * `jsx-a11y` n'a rien à dire d'un `.ts` sans balise, et l'y appliquer ferait
 * porter le verdict par des fichiers qui ne peuvent pas le déclencher.
 *
 * Deux règles restent **`off` dans le preset lui-même** et le restent ici
 * (`anchor-ambiguous-text`, `control-has-associated-label`) : les rallumer est
 * le pas vers `strict`, que ce lot n'a pas pris — la cible arrêtée en #471 est
 * AA, pas AAA (docs/30 §3.5).
 */
const eslintConfig = defineConfig([
  ...nextVitals,
  ...nextTs,
  {
    files: ["**/*.jsx", "**/*.tsx"],
    rules: jsxA11y.flatConfigs.recommended.rules,
  },
  // Override default ignores of eslint-config-next.
  globalIgnores([
    // Default ignores of eslint-config-next:
    ".next/**",
    "out/**",
    "build/**",
    "next-env.d.ts",
  ]),
]);

export default eslintConfig;

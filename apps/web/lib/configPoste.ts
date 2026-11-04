/**
 * La **configuration du poste** : où est l'API, et le jeton qui l'ouvre — lus au
 * moment où le front est **servi**, jamais au moment où il est **construit** (#641).
 *
 * Un paquet d'installation se construit **une fois** et se pose sur **N** postes.
 * Or le jeton de l'API locale (#638) est propre à chaque poste — l'API l'engendre
 * dans `~/.maestro/jeton-api` au premier démarrage —, et `NEXT_PUBLIC_*` est inliné
 * dans le JavaScript par `next build` : un jeton posé là serait celui du poste qui
 * a construit le paquet, c'est-à-dire un secret publié avec lui, et faux partout
 * ailleurs. L'URL de l'API a le même défaut, en moins grave : figée au build, elle
 * interdit au lanceur de servir l'API ailleurs que là où le paquet l'attendait.
 *
 * D'où deux moitiés :
 *
 * - **le serveur** du front lit, à chaque requête, ce que son lanceur lui a donné
 *   (`MAESTRO_API_URL`, `MAESTRO_API_JETON` — sans préfixe `NEXT_PUBLIC_`, donc
 *   jamais inlinés) et le pose **dans le document** (`app/layout.tsx`) ;
 * - **la page** le relit au moment de chaque appel (`configPoste`), et retombe sur
 *   les valeurs du build quand le serveur n'a rien posé — ce que font
 *   `scripts/controltower/start.sh` et `next dev`, inchangés.
 *
 * ⚠ **Dans le document, et jamais dans un script servi à part.** Un `<script src>`
 * se charge depuis n'importe quelle origine : une page tierce qui l'inclurait
 * lirait le jeton dans `window` aussi sûrement que nous. Le document HTML, lui,
 * ne se lit pas d'ailleurs (CORS) et ne s'exécute pas comme un script — c'est la
 * même exposition que celle du jeton inliné dans les morceaux du build, pas une de
 * plus.
 */

/** Le nom sous lequel le document pose la configuration, et le seul endroit qui l'écrit. */
export const CLE_CONFIG_POSTE = "__MAESTRO_POSTE__";

/** Ce que le serveur a reçu de son lanceur. Une clé absente : le build fait foi. */
export type ConfigPoste = {
  readonly api?: string;
  readonly jeton?: string;
};

type Environnement = Readonly<Record<string, string | undefined>>;

/**
 * Côté serveur : la configuration que le lanceur a donnée au serveur du front.
 *
 * À n'appeler que pendant un rendu **dynamique** (après `connection()`) : lue
 * pendant un rendu statique, elle serait celle du build, figée dans le HTML.
 */
export function configPosteServeur(env: Environnement = process.env): ConfigPoste {
  const api = (env.MAESTRO_API_URL ?? "").trim();
  const jeton = (env.MAESTRO_API_JETON ?? "").trim();
  return {
    ...(api !== "" ? { api } : {}),
    ...(jeton !== "" ? { jeton } : {}),
  };
}

/**
 * Le script qui pose la configuration dans le document — `null` quand il n'y a
 * rien à poser, pour que la page reste **exactement** celle d'avant ce lot.
 *
 * `<` est échappé : une valeur qui contiendrait `</script>` fermerait sinon la
 * balise au milieu du JSON, et ce qui suit s'exécuterait comme du HTML.
 */
export function scriptConfigPoste(config: ConfigPoste): string | null {
  if (config.api === undefined && config.jeton === undefined) return null;
  const json = JSON.stringify(config).replace(/</g, "\\u003c");
  return `window.${CLE_CONFIG_POSTE}=${json};`;
}

/**
 * Ce que la page a reçu — lu **à chaque appel**, jamais figé à l'import (le même
 * parti que `jetonApi`, et pour la même raison : un test doit pouvoir le poser).
 *
 * Hors navigateur (rendu serveur d'un composant client), c'est l'environnement du
 * serveur qui répond : les deux côtés rendent alors la même valeur, et
 * l'hydratation ne voit pas d'écart.
 */
export function configPoste(): ConfigPoste {
  if (typeof window === "undefined") return configPosteServeur();
  const posee = (window as unknown as Record<string, unknown>)[CLE_CONFIG_POSTE];
  if (posee === null || typeof posee !== "object") return {};
  const { api, jeton } = posee as Record<string, unknown>;
  return {
    ...(typeof api === "string" && api.trim() !== "" ? { api: api.trim() } : {}),
    ...(typeof jeton === "string" && jeton.trim() !== ""
      ? { jeton: jeton.trim() }
      : {}),
  };
}

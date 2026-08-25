/**
 * La sonde d'accessibilité de la suite (#537, lot 5 de #532).
 *
 * `axe-core` était **déjà dans le dépôt** avant ce lot — en transitif, tiré par
 * `eslint-plugin-jsx-a11y` — et **jamais importé** (docs/30 §3.4) : le filet
 * manquant n'était pas l'outil, c'était son branchement. Ce module est ce
 * branchement, et rien d'autre ; ce qu'on en fait vit dans `tests/a11y.test.tsx`.
 *
 * ⚠ `vitest-axe` **n'a pas été retenu**, alors que le ticket le nomme en
 * premier : il n'expose qu'un `expect().toHaveNoViolations()` au-dessus de
 * `axe.run`, ne sait pas trancher par impact — le verdict demandé porte sur
 * `serious`/`critical`, pas sur « zéro violation » — et ajouterait une
 * dépendance dont le pair déclaré est Vitest 0.x. Le ticket prévoyait la
 * bifurcation (« ou `axe-core` branché sur la suite ») ; c'est celle-ci.
 *
 * **Le contexte est le document entier, pas le conteneur rendu**, et ce n'est
 * pas un détail : axe distingue les règles de *page* (`region`, `bypass`,
 * `landmark-one-main`, `page-has-heading-one`) des règles de nœud, et ne joue
 * les premières que si on lui donne la page. Les passer à côté reviendrait à
 * auditer des composants là où le ticket demande d'auditer des **écrans**.
 */

import axe, { type Result, type RunOptions } from "axe-core";

/**
 * Le seuil du verdict, tel que le ticket l'arrête : `serious` et `critical`.
 *
 * Les `moderate`/`minor` sont **relevés et rendus** dans le message d'échec
 * (voir `raconter`) mais ne font pas rougir : un seuil qu'on ne peut pas tenir
 * est un seuil qu'on finit par contourner, et la cible de #471 est AA — pas la
 * perfection d'un audit.
 */
export const IMPACTS_BLOQUANTS: ReadonlySet<string> = new Set([
  "serious",
  "critical",
]);

/**
 * Les règles écartées, et **la raison de chacune**. Une exclusion sans motif est
 * une violation qu'on a décidé de ne plus voir.
 *
 * - `color-contrast` — jsdom ne calcule **aucune** couleur rendue : la règle
 *   rendrait « incomplete » sur tout, ou pire, du vert par construction. Le
 *   contraste est gardé ailleurs, et mieux : `tests/contraste.test.ts` (#534)
 *   mesure les 36 paires légitimes **par thème** sur les octets de
 *   `globals.css`, ce qu'un audit de DOM simulé ne saurait pas faire.
 * - `html-has-lang` / `html-lang-valid` / `html-xml-lang-mismatch` /
 *   `document-title` — ces quatre-là ne jugent pas un écran mais le **document
 *   qui l'enveloppe** : `lang` vit sur le `<html>` de `app/layout.tsx` et le
 *   titre sort de son `export const metadata`, deux choses que le rendu d'un
 *   composant ne monte pas — jsdom sert son squelette par défaut, sans l'un ni
 *   l'autre. Les garder ici ferait rapporter une faute du **harnais** comme une
 *   faute du produit, sur les dix écrans à la fois. Elles ne disparaissent pas
 *   pour autant, elles changent de juge : `tests/a11y.test.tsx` les vérifie sur
 *   la source du layout, même méthode que la sonde de contraste sur
 *   `globals.css`.
 */
const REGLES_ECARTEES: RunOptions["rules"] = {
  "color-contrast": { enabled: false },
  "html-has-lang": { enabled: false },
  "html-lang-valid": { enabled: false },
  "html-xml-lang-mismatch": { enabled: false },
  "document-title": { enabled: false },
};

/** Joue axe sur la page montée et rend **toutes** ses violations, tous impacts. */
export async function auditerLaPage(): Promise<Result[]> {
  const resultat = await axe.run(document, {
    resultTypes: ["violations"],
    rules: REGLES_ECARTEES,
  });
  return resultat.violations;
}

/** Ce qui fait rougir : les violations au-dessus du seuil du ticket. */
export function bloquantes(violations: readonly Result[]): Result[] {
  return violations.filter((v) => IMPACTS_BLOQUANTS.has(v.impact ?? ""));
}

/**
 * Le récit d'un échec — c'est lui qui décide du coût de la correction.
 *
 * Un `expect(bloquantes).toHaveLength(0)` nu rend « expected 3 to be 0 » : le
 * nombre, jamais la faute. On rend donc la règle, son impact, sa phrase et **le
 * premier nœud fautif**, et on **liste aussi** ce qui n'a pas fait rougir : une
 * violation `moderate` d'aujourd'hui est la `serious` du prochain remaniement,
 * et la connaître sans la subir est le seul régime tenable.
 */
export function raconter(violations: readonly Result[]): string {
  if (violations.length === 0) return "aucune violation";
  return violations
    .map((v) => {
      const cible = v.nodes[0]?.html ?? "";
      const abrege = cible.length > 120 ? `${cible.slice(0, 117)}…` : cible;
      return `  [${v.impact ?? "?"}] ${v.id} — ${v.help} (${v.nodes.length} nœud(s))\n    ${abrege}`;
    })
    .join("\n");
}

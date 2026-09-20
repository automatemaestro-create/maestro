"use client";

/**
 * **Ce que la fenêtre sait faire et qu'un onglet ne sait pas** (#928, lot 7 de
 * #921) — aujourd'hui une seule chose : ouvrir un dossier dans l'explorateur du
 * système.
 *
 * ## Pourquoi ce module existe, et pourquoi il n'est pas un `if (electron)`
 *
 * **ENF-12 tient** (docs/35 §2.5) : *aucun embranchement de code applicatif*,
 * pas de `if (electron)` dans `apps/web/**`. Ce module ne teste donc **jamais**
 * l'environnement — ni `navigator.userAgent`, ni `process.versions`, ni un
 * drapeau posé par la coque. Il teste une **capacité** : la fonction est-elle
 * là ? C'est le motif de `navigator.clipboard` et de `showOpenFilePicker`, et
 * la différence n'est pas de style :
 *
 * - un test de plateforme demande « où suis-je ? » et oblige le front à
 *   connaître ses coques, donc à changer chaque fois qu'il y en a une de plus ;
 * - un test de capacité demande « puis-je ? » et n'en connaît aucune. La même
 *   page, servie dans un onglet, répond « non » et prend l'autre chemin — qui
 *   n'est pas un pis-aller, mais le second geste (voir ci-dessous).
 *
 * C'est aussi ce que docs/35 §2.4 annonçait : l'ouverture de l'explorateur de
 * fichiers est l'une des **trois capacités qui justifient une fenêtre**, et le
 * lot 7 est celui qui la réclame. La coque expose donc son premier pont
 * (`apps/desktop/preload.js`), minimal et nommé — un seul verbe.
 *
 * ## Le repli web n'est pas une dégradation
 *
 * Les notes techniques du ticket le demandent : hors de la fenêtre, « le chemin
 * reste **affiché et copiable** ». C'est le parti pris 4 de la veille, d'après
 * GitLab CI qui pose toujours **deux** gestes sur un artefact — « Browse » (y
 * aller) et « Download » (l'emporter). Chez nous : *ouvrir le dossier* quand on
 * le peut, *copier le chemin* **toujours**. Le second n'est donc pas le lot de
 * consolation du premier ; il est présent dans les deux régimes, et c'est lui
 * qui rend le chemin utilisable dans un terminal, un explorateur, un ticket.
 *
 * ## Ce que ce module ne garantit pas
 *
 * Que le dossier s'ouvre : c'est la coque qui décide, et elle **refuse** ce qui
 * n'est pas un répertoire existant (voir `apps/desktop/main.js` — `openPath`
 * sur un exécutable le lancerait). `ouvrirDossier` rend donc un booléen, et
 * l'appelant dit à l'écran quand c'est non : un geste sans effet ni message est
 * ce qu'on ne saurait pas distinguer d'une page figée.
 */

/**
 * Le pont que la coque de bureau expose, quand il y en a une.
 *
 * Optionnel de bout en bout : `window.maestro` est absent dans un onglet, et
 * `ouvrirDossier` pourrait manquer d'une version de coque à l'autre — on teste
 * donc la **fonction**, pas l'objet.
 */
declare global {
  interface Window {
    maestro?: {
      ouvrirDossier?: (chemin: string) => Promise<boolean>;
    };
  }
}

/** Le poste peut-il ouvrir un dossier ? (faux dans un onglet de navigateur) */
export function peutOuvrirDossier(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.maestro?.ouvrirDossier === "function"
  );
}

/**
 * Ouvre `chemin` dans l'explorateur du système — `false` si ce n'est pas
 * possible, ou si la coque a refusé.
 *
 * Ne lève pas : un pont qui rejette (coque d'une autre version, IPC coupé) vaut
 * « non », et l'appelant a déjà le chemin sous les yeux.
 */
export async function ouvrirDossier(chemin: string): Promise<boolean> {
  const pont = window.maestro?.ouvrirDossier;
  if (typeof pont !== "function") return false;
  try {
    return await pont(chemin);
  } catch {
    return false;
  }
}

/**
 * Copie `texte` dans le presse-papiers — `false` si le navigateur refuse.
 *
 * `navigator.clipboard` demande un contexte sécurisé : `localhost` en est un
 * (spécification HTML), donc la stack locale l'a, servie en clair comme dans la
 * fenêtre. Un refus reste possible (permission, navigateur ancien) et se dit à
 * l'écran plutôt que de se taire.
 */
export async function copierTexte(texte: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(texte);
    return true;
  } catch {
    return false;
  }
}

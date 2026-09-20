"use client";

/**
 * **Ce que la fenêtre sait faire et qu'un onglet ne sait pas** (#928 lot 7,
 * #938 lot 8, de #921) — trois choses, et les deux dernières répondent à la
 * même question :
 *
 * 1. **ouvrir un dossier** dans l'explorateur du système (`ouvrirDossier`) ;
 * 2. **ouvrir le dialogue de dossier de l'OS** sans détour par le backend
 *    (`choisirDossierDuPoste`) ;
 * 3. **lire le chemin réel d'un dossier déposé** (`cheminDuDossierDepose`).
 *
 * ## Pourquoi 2 et 3 ne doublent pas l'explorateur de l'API
 *
 * Un navigateur ne livre **jamais** de chemin absolu : c'est la contrainte qui a
 * fait naître l'explorateur servi par l'API (#223) puis le dialogue ouvert par
 * le backend (#278). Ce dernier porte ses limites en toutes lettres — il refuse
 * quand la requête vient du réseau (`selecteur-hors-poste` : le dialogue
 * s'ouvrirait sur le serveur, devant personne) et quand le poste n'a ni
 * PowerShell, ni `osascript`, ni `zenity`/`kdialog` (`selecteur-sans-outil`).
 * **Dans une fenêtre, ces deux empêchements n'existent pas** : le dialogue est
 * celui d'Electron, il s'ouvre là où la personne regarde, et il ne dépend
 * d'aucun outil installé.
 *
 * Et le **dépôt d'un dossier**, lui, n'existait nulle part : aucun navigateur ne
 * donne le chemin de ce qu'on lui dépose, ce qu'annonçait déjà le cadrage de
 * docs/24 §4.7.
 *
 * ⚠ **Aucune de ces trois fonctions n'autorise quoi que ce soit.** Le chemin
 * rendu est celui de l'OS, brut : c'est `POST /api/projets/racine` qui le
 * confronte aux frontières d'EF-38 (#221), au même endroit que toutes les autres
 * voies. Une validation côté coque ferait deux formules à tenir d'accord, et
 * c'est la garde qui perdrait.
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
      choisirDossier?: (depart: string | null) => Promise<string | null>;
      cheminDuFichier?: (fichier: File) => string | null;
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
 * Le poste peut-il ouvrir **lui-même** le dialogue de dossier de l'OS ? (#938)
 *
 * Vrai dans la fenêtre, faux dans un onglet — où l'écran retombe sur la
 * disponibilité que l'API rend (`GET /api/projets/selecteur`) et ses trois
 * motifs, inchangés. La question est bien « puis-je ? » et non « où suis-je ? ».
 */
export function peutChoisirDossier(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.maestro?.choisirDossier === "function"
  );
}

/**
 * Ouvre le dialogue de dossier du poste et rend le chemin choisi — `null` si la
 * personne a **annulé**, ce qui est un geste normal et n'affiche rien.
 *
 * `depart` est le dossier d'ouverture : un confort, jamais une permission. Le
 * chemin rendu est celui de l'OS, **non canonicalisé** — il reste à le faire
 * juger par `verdictRacine`.
 */
export async function choisirDossierDuPoste(
  depart: string | null = null,
): Promise<string | null> {
  const pont = window.maestro?.choisirDossier;
  if (typeof pont !== "function") return null;
  try {
    return await pont(depart);
  } catch {
    return null;
  }
}

/**
 * Le poste sait-il donner le **chemin réel** d'un dossier déposé ? (#938)
 *
 * C'est ce qui décide si l'écran de déclaration **dessine** sa zone de dépôt :
 * une cible visible là où le geste est impossible promettrait ce qu'aucun
 * navigateur ne peut tenir (réserve du regard neuf sur la variante retenue,
 * et troisième critère d'acceptation du ticket — *hors fenêtre, rien ne
 * change*).
 */
export function peutLireCheminDepose(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.maestro?.cheminDuFichier === "function"
  );
}

/**
 * Le chemin réel du `File` déposé, ou `null` si le poste ne sait pas le dire.
 *
 * `null` couvre les deux cas d'un seul tenant — pas de pont (onglet), et un
 * objet qui ne vient pas du disque (glissé depuis une autre page) : l'appelant
 * n'a qu'une chose à afficher, « ce dépôt n'a pas donné de chemin ».
 */
export function cheminDuDossierDepose(fichier: File): string | null {
  const pont = window.maestro?.cheminDuFichier;
  if (typeof pont !== "function") return null;
  try {
    return pont(fichier) || null;
  } catch {
    return null;
  }
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

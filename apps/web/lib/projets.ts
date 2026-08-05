/**
 * Le vocabulaire de l'écran Projets (#225) : ce qui traduit les codes de l'API
 * (#223) en mots d'interface, et ce qui convertit un périmètre entre sa forme
 * de saisie (une ligne) et sa forme d'API (une liste).
 *
 * Séparé des composants parce que c'est de la **donnée**, pas du rendu : les
 * motifs de refus sont un ensemble fermé publié par docs/05 §6.7, et les tester
 * ne devrait pas demander de monter un DOM.
 */

/** Les motifs par défaut du modèle (#221), montrés quand la saisie est vide. */
export const PERIMETRE_PAR_DEFAUT = {
  inclus: ["."],
  exclus: [".git", "node_modules", ".env", "**/secrets/**"],
};

/**
 * Ce que dit l'écran d'une `origine`.
 *
 * Volontairement décorrélé du VCS : « existant » veut dire « on reprend un
 * dossier qui est là », pas « c'est un dépôt Git » — un dossier repris peut ne
 * pas être versionné, et un dossier créé peut l'être ensuite. Le « dépôt
 * existant » du besoin se lit donc en deux temps, l'origine et la pastille
 * `vcs` : les fusionner ferait mentir l'une des deux.
 */
export function libelleOrigine(origine: string): string {
  if (origine === "nouveau") return "Nouveau dossier";
  if (origine === "existant") return "Dossier existant";
  return origine;
}

/**
 * Le conseil attaché à un motif de refus — ce que l'utilisateur peut **faire**,
 * là où le `message` du backend dit ce qui s'est passé (EF-38).
 *
 * Le message de l'API est toujours affiché : ceci ne le remplace pas, il le
 * prolonge. Un motif inconnu (l'API en gagnera) ne rend rien plutôt qu'une
 * phrase inventée — l'écran reste juste, seulement moins aidant.
 */
const CONSEILS: Record<string, string> = {
  "racine-de-disque":
    "Descendre d'un cran : une racine de projet est un dossier de travail, pas un disque entier.",
  "dossier-utilisateur-nu":
    "Choisir un sous-dossier : le dossier utilisateur au complet est trop large pour un projet.",
  "au-dessus-du-dossier-utilisateur":
    "Choisir un dossier dans l'espace de l'utilisateur, pas au-dessus.",
  "chemin-sensible":
    "Zone protégée (clés, données d'application) : elle n'est ni explorable ni déclarable.",
  "chemin-systeme": "Dossier système : Maestro n'y travaille pas.",
  "depot-maestro": "Maestro ne se prend pas lui-même pour projet.",
  "au-dessus-du-depot-maestro":
    "Ce dossier contient le dépôt de Maestro : choisir un dossier à côté, pas au-dessus.",
  "hors-racines-explorables":
    "Élargir les dossiers explorables avec MAESTRO_EXPLORATEUR_RACINES (séparateur « ; » sous Windows, « : » sous POSIX), puis relancer le backend.",
  "aucune-racine-explorable":
    "Aucun dossier explorable n'est configuré : renseigner MAESTRO_EXPLORATEUR_RACINES, puis relancer le backend.",
  "acces-refuse": "Le système d'exploitation refuse d'ouvrir ce dossier.",
  "dossier-absent":
    "Le dossier n'est pas là — l'origine « Nouveau dossier » le crée à la déclaration.",
  "pas-un-dossier": "Ce chemin désigne un fichier : il faut un dossier.",
  "projet-inconnu":
    "Ce projet n'est plus déclaré — recharger la liste pour repartir de l'état réel.",
  "projet-illisible":
    "Le fichier de ce projet ne se relit pas : il est ignoré par la liste tant qu'il n'est pas réparé ou supprimé.",
};

/** Le conseil d'un motif, ou `null` s'il n'y en a pas pour celui-ci. */
export function conseilMotif(motif: string): string | null {
  return CONSEILS[motif] ?? null;
}

/**
 * Les motifs saisis sur une ligne, en liste d'API. Une saisie vide rend `null`,
 * qui vaut « laisse les défauts du modèle » côté backend — à distinguer d'une
 * liste vide, qui voudrait dire « n'inclus rien ».
 */
export function motifsDepuisTexte(texte: string): string[] | null {
  const motifs = texte
    .split(",")
    .map((motif) => motif.trim())
    .filter((motif) => motif !== "");
  return motifs.length === 0 ? null : motifs;
}

/** Une liste de motifs en ligne de saisie. */
export function texteDepuisMotifs(motifs: string[]): string {
  return motifs.join(", ");
}

/**
 * Le nom de dossier d'un chemin POSIX — de quoi pré-remplir le nom d'un projet
 * dès qu'une racine est choisie, sans que l'utilisateur ait à le recopier.
 */
export function nomDepuisChemin(chemin: string): string {
  const segments = chemin.split("/").filter((segment) => segment !== "");
  return segments.length === 0 ? chemin : segments[segments.length - 1];
}

/**
 * Le chemin d'un dossier **à créer** sous un parent : c'est ainsi que l'origine
 * « nouveau » évite la saisie d'un chemin absolu (critère #225). L'utilisateur
 * ne tape qu'un **nom de dossier**, le parent venant toujours de l'explorateur.
 *
 * Un nom vide rend le parent inchangé, et un nom qui contient un séparateur est
 * refusé par l'appelant : le seul chemin absolu du formulaire reste celui que
 * l'API a énuméré.
 */
export function cheminEnfant(parent: string, nom: string): string {
  const propre = nom.trim();
  if (propre === "") return parent;
  return `${parent.replace(/\/+$/, "")}/${propre}`;
}

/** Vrai si ce nom de dossier est saisissable tel quel (pas un bout de chemin). */
export function nomDossierValide(nom: string): boolean {
  const propre = nom.trim();
  return propre !== "" && !/[/\\]/.test(propre) && propre !== "." && propre !== "..";
}

/** Formatages partagés de l'UI : coûts, heures, libellés de statut. */

/**
 * Les montants se lisent à **deux décimales** (#247) : c'est la précision d'un
 * relevé de dépense. Quatre décimales rendaient « 1,2345 $US » partout — tuile
 * Dépense, cartes du Kanban, Coûts & analytics, répartition par agent —, un
 * chiffre qu'on déchiffre au lieu de le lire.
 *
 * Ce module est la **source unique** du rendu des montants : un composant qui
 * reformate dans son coin fait diverger l'écran de lui-même.
 */
const FORMAT_COUT = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Le plus petit montant que deux décimales savent écrire. En dessous de sa
 * moitié, l'arrondi rendrait « 0,00 $US » — indiscernable d'une dépense nulle,
 * alors que le cas est **courant** sur un fournisseur local (#113), où un appel
 * coûte quelques dix-millièmes de dollar.
 */
const CENTIME = 0.01;

/**
 * Un coût en dollars, ou « — » si aucun coût n'a été rapporté (inconnu ≠ nul).
 *
 * Trois cas distincts, et c'est le point : « — » (rien n'a été rapporté),
 * « 0,00 $US » (zéro rapporté, une vraie mesure) et « < 0,01 $US » (une dépense
 * réelle mais trop petite pour ce format). Les confondre ferait passer un
 * fournisseur bon marché pour un fournisseur gratuit.
 */
export function formatCout(cout: number | null): string {
  if (cout === null) return "—";
  if (cout > 0 && cout < CENTIME / 2) return `< ${FORMAT_COUT.format(CENTIME)}`;
  return FORMAT_COUT.format(cout);
}

/**
 * Les graduations d'un axe de coûts vivent ici aussi, bien qu'elles ne suivent
 * pas la règle des deux décimales : c'est le seul endroit où elle ne tient pas —
 * sur une série de quelques millièmes de dollar, **toutes** les graduations
 * tomberaient sur « 0,00 » et l'axe ne dirait plus rien. L'exception est
 * déclarée ici plutôt que laissée au composant qui dessine (#247).
 */
const FORMAT_MONTANT_AXE = new Intl.NumberFormat("fr-FR", {
  maximumFractionDigits: 4,
});

/**
 * La graduation d'un axe de coûts, symbole compris (« 0,25 $ »). L'espace y est
 * insécable : une graduation ne se coupe pas en deux.
 */
export function formatCoutAxe(valeur: number): string {
  return `${FORMAT_MONTANT_AXE.format(valeur)} $`;
}

const FORMAT_TOKENS = new Intl.NumberFormat("fr-FR");

/** Un compte de tokens avec séparateur de milliers (« 12 345 »). */
export function formatTokens(tokens: number): string {
  return FORMAT_TOKENS.format(tokens);
}

const FORMAT_SECONDES = new Intl.NumberFormat("fr-FR", {
  maximumFractionDigits: 1,
});

/** Une durée en millisecondes rendue lisible, ou « — » si non rapportée. */
export function formatDuree(dureeMs: number | null): string {
  if (dureeMs === null) return "—";
  if (dureeMs < 1000) return `${dureeMs} ms`;
  const secondesTotales = Math.round(dureeMs / 1000);
  if (secondesTotales < 60) return `${FORMAT_SECONDES.format(dureeMs / 1000)} s`;
  const minutes = Math.floor(secondesTotales / 60);
  const secondes = secondesTotales % 60;
  return `${minutes} min ${String(secondes).padStart(2, "0")} s`;
}

/** L'heure locale d'un horodatage ISO du backend (chaîne vide si absent). */
export function formatHeure(horodatage: string): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  return date.toLocaleTimeString("fr-FR");
}

/** La date et l'heure locales d'un horodatage ISO du backend (chaîne vide si absent). */
export function formatDateHeure(horodatage: string): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  return date.toLocaleString("fr-FR");
}

/** Libellés français des statuts de tâche (machine à états docs/03 §3). */
const LIBELLES_STATUT: Record<string, string> = {
  assignee: "Assignée",
  en_cours: "En cours",
  bloquee: "Bloquée",
  terminee: "Terminée",
  echec: "Échec",
  approuve: "Approuvée",
  refuse: "Refusée",
  refus_outil: "Outil refusé",
};

/** Le libellé d'un statut, ou le statut brut si le flux s'est enrichi. */
export function libelleStatut(statut: string): string {
  return LIBELLES_STATUT[statut] ?? statut;
}

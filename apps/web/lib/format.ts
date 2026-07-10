/** Formatages partagés de l'UI : coûts, heures, libellés de statut. */

const FORMAT_COUT = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/** Un coût en dollars, ou « — » si aucun coût n'a été rapporté (inconnu ≠ nul). */
export function formatCout(cout: number | null): string {
  return cout === null ? "—" : FORMAT_COUT.format(cout);
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

/** Libellés français des statuts de tâche (machine à états docs/03 §3). */
const LIBELLES_STATUT: Record<string, string> = {
  assignee: "Assignée",
  en_cours: "En cours",
  bloquee: "Bloquée",
  terminee: "Terminée",
  echec: "Échec",
  approuve: "Approuvée",
  refuse: "Refusée",
};

/** Le libellé d'un statut, ou le statut brut si le flux s'est enrichi. */
export function libelleStatut(statut: string): string {
  return LIBELLES_STATUT[statut] ?? statut;
}
